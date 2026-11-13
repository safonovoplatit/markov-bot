from collections import Counter
import random
import re
from html.parser import HTMLParser
from pathlib import Path


class TelegramExport(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []
        self.messages = []

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "text" in dict(attrs).get("class", "").split():
                self.depth = 1
        elif tag == "br" and self.depth:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1
            if not self.depth:
                message = " ".join("".join(self.parts).split())
                if message:
                    self.messages.append(message)
                self.parts.clear()

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


# Each pair combines interchangeable openings and endings for one theme.
THEMES = {
    "свои": (
        r"брат\w*|друг\w*|друз\w*|пацан\w*|своих",
        ("Своих видно, когда начинается движ", "Братство проверяется, когда всё летит к чертям",
         "На словах каждый второй тебе брат", "Когда темнеет, круг становится меньше"),
        ("остаются те, кому даже звонить не надо.", "пустые обещания быстро стихают.",
         "один рядом стоит целой толпы.", "тут и понимаешь, с кем тебе по пути."),
    ),
    "дело": (
        r"дел\w*|работ\w*|слов\w*|обещ\w*|ответ\w*",
        ("Громко говорить любой умеет", "Сказал, что вывезешь", "Уважение на районе не выпрашивают",
         "Хочешь, чтобы твоё слово весило"),
        ("покажи это делом.", "тогда и лишних разговоров не будет.",
         "пусть за тебя ответят поступки.", "тут всё решает то, что ты сделал."),
    ),
    "дорога": (
        r"дорог\w*|путь|район\w*|движ\w*|ед\w*|ехал\w*",
        ("Свой путь редко бывает ровным", "Дорога длинная, а вечер короткий",
         "С района можно уехать", "Свой поворот узнаешь сам"),
        ("помни, с чего начинал.", "главное, не потеряй себя по дороге.",
         "чужая карта тут не поможет.", "держи курс, даже если рядом стало тихо."),
    ),
    "выдержка": (
        r"жизн\w*|сил\w*|вывез\w*|тяж\w*|холод\w*|страх\w*",
        ("Бывает, день с порога идёт наперекосяк", "Даже крепких иногда шатает",
         "Когда жизнь давит со всех сторон", "Если сегодня не вывез"),
        ("соберись и сделай следующий шаг.", "выдохни и продолжай своё.",
         "оставь себе время собраться.", "утром попробуй ещё раз."),
    ),
    "тишина": (
        r"тиш\w*|молч\w*|ноч\w*|один\w*|спокой\w*",
        ("Ночью район звучит по-другому", "Иногда лучше помолчать",
         "Когда вокруг слишком много шума", "Тихие люди тоже многое вывозят"),
        ("в тишине лучше слышно своих.", "лишние слова только сбивают с толку.",
         "дай делам сказать за тебя.", "в тишине проще понять, чего хочешь."),
    ),
}


class QuoteGenerator:
    def __init__(self, messages, supplemental=()):
        self.patterns = {
            name: re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
            for name, (pattern, _, _) in THEMES.items()
        }
        self.weights = dict.fromkeys(THEMES, 0)
        self.source = {self.normalize(message) for message in (*messages, *supplemental)}
        self.primary_phrases = self.extract_phrases(messages)
        self.supplemental_phrases = self.extract_phrases(supplemental)
        source = set()
        vocabulary = Counter()
        for message in messages:
            normalized = self.normalize(message)
            if normalized in source:
                continue
            source.add(normalized)
            vocabulary.update(normalized.split())
            for name, pattern in self.patterns.items():
                self.weights[name] += bool(pattern.search(message))
        variants = (
            ("брат", "братан", "братишка"),
            ("толпы", "народа"),
            ("движ", "движуха", "кипиш"),
            ("вывезешь", "справишься"),
            ("вывозят", "справляются"),
            ("районе", "дворе"),
            ("шум", "гвалт"),
        )
        preferred = {
            words[0]: max(words, key=vocabulary.__getitem__)
            for words in variants if any(word in vocabulary for word in words)
        }

        def adapt(quote):
            return re.sub(r"\b[а-яё]+\b", lambda match: preferred.get(
                match.group(), match.group()), quote)

        self.quotes = {
            name: [adapt(f"{opening} — {ending}") for opening in openings for ending in endings
                   if self.normalize(adapt(f"{opening} — {ending}")) not in self.source]
            for name, (_, openings, endings) in THEMES.items()
        }

    @staticmethod
    def normalize(text):
        return " ".join(re.findall(r"\w+", text.casefold().replace("ё", "е")))

    @classmethod
    def extract_phrases(cls, messages):
        phrases = {}
        for message in messages:
            if re.search(r"https?://|www\.|[@<>]|\d", message, re.IGNORECASE):
                continue
            for sentence in re.split(r"[.!?…]+(?:\s+|$)", message):
                if re.search(r"\b(?:кто|чей|почему|зачем|когда же)\b", sentence, re.IGNORECASE):
                    continue
                phrase = sentence.strip(' ,:;—–-«»"')
                words = cls.normalize(phrase).split()
                if 4 <= len(words) <= 18 and re.fullmatch(r"[а-яё][а-яё ,:;—–-]*[а-яё]", phrase, re.IGNORECASE):
                    phrases.setdefault(" ".join(words), phrase)
        return tuple(phrases.values())

    def from_phrases(self, phrases, seed_words, themes, excluded, used_phrases):
        if seed_words or themes:
            phrases = [
                phrase for phrase in phrases
                if seed_words.intersection(self.normalize(phrase).split())
                or any(self.patterns[name].search(phrase) for name in themes)
            ]
        candidates = list(phrases)
        random.shuffle(candidates)
        if not seed_words and not themes:
            # Prefer self-contained observations for automatic posts.
            candidates.sort(key=lambda phrase: bool(re.search(
                r"\b(?:каждый|никогда|всегда|главное|иногда|люди|жизнь|друзья|пацаны)\b",
                phrase, re.IGNORECASE,
            )), reverse=True)
        related = {name: [] for name in self.patterns}
        for phrase in candidates:
            if self.normalize(phrase) in used_phrases:
                continue
            for name, pattern in self.patterns.items():
                if pattern.search(phrase):
                    related[name].append(phrase)

        for phrase in candidates:
            if self.normalize(phrase) in used_phrases:
                continue
            opening = phrase[0].upper() + phrase[1:] + "."
            quote = opening
            for name, pattern in self.patterns.items():
                if not pattern.search(phrase):
                    continue
                ending = next((line for line in related[name] if line != phrase), None)
                if ending:
                    quote += " " + ending[0].upper() + ending[1:] + "."
                    break
            if self.normalize(quote) in excluded:
                continue
            return quote
        return None

    def generate(self, seed="", exclude=()):
        seed = seed.strip().casefold()
        excluded = {self.normalize(quote.split("\n\n", 1)[0]) for quote in exclude}
        used_phrases = {
            self.normalize(part)
            for quote in exclude
            for part in re.split(r"\.\s+", quote.split("\n\n", 1)[0])
        }
        seed_words = set(self.normalize(seed).split())
        themes = [name for name, pattern in self.patterns.items() if name == seed or pattern.search(seed)]
        for words, requested_themes in ([(seed_words, themes)] if seed else []) + [(set(), [])]:
            for phrases in (self.primary_phrases, self.supplemental_phrases):
                quote = self.from_phrases(phrases, words, requested_themes, excluded, used_phrases)
                if quote:
                    return quote

        available = {
            name: [quote for quote in quotes if self.normalize(quote) not in excluded | self.source]
            for name, quotes in self.quotes.items()
        }
        names = [name for name in themes if available[name]] or [
            name for name, quotes in available.items() if quotes
        ]
        if not names:
            raise ValueError("Все варианты цитат использованы.")
        name = random.choices(names, weights=[1 + self.weights[name] ** 0.5 for name in names])[0]
        return random.choice(available[name])


def load_quotes(paths):
    primary = []
    supplemental = []
    for path in map(Path, paths):
        parser = TelegramExport()
        parser.feed(path.read_text(encoding="utf-8-sig"))
        parser.close()
        target = primary if path.name.casefold() == "messages.html" else supplemental
        target.extend(parser.messages)
    count = len(primary) + len(supplemental)
    if not count:
        raise ValueError("В HTML-файлах не найдено текстовых сообщений Telegram.")
    return QuoteGenerator(primary or supplemental, supplemental if primary else ()), count
