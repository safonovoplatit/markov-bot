import json
import os
import random
import re
from dataclasses import dataclass, field
from typing import Dict, List

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9']+|[.,!?;:]")


def tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(text)


def detokenize(tokens: List[str]) -> str:
    out = []
    for t in tokens:
        if t in ".,!?;:":
            if out:
                out[-1] = out[-1] + t
            else:
                out.append(t)
        else:
            out.append(t)
    return " ".join(out)


@dataclass
class MarkovModel:
    transitions: Dict[str, Dict[str, int]] = field(default_factory=dict)
    starts: Dict[str, int] = field(default_factory=dict)

    def add_tokens(self, tokens: List[str]) -> None:
        if not tokens:
            return
        first = tokens[0]
        self.starts[first] = self.starts.get(first, 0) + 1
        for a, b in zip(tokens, tokens[1:]):
            nxt = self.transitions.setdefault(a, {})
            nxt[b] = nxt.get(b, 0) + 1

    def add_text(self, text: str) -> None:
        tokens = tokenize(text)
        if tokens:
            self.add_tokens(tokens)

    def _weighted_choice(self, options: Dict[str, int]) -> str:
        total = sum(options.values())
        r = random.randint(1, total)
        upto = 0
        for k, v in options.items():
            upto += v
            if upto >= r:
                return k
        return random.choice(list(options.keys()))

    def generate(self, seed_tokens: List[str], min_len: int = 8, max_len: int = 30) -> List[str]:
        if not self.starts:
            return []

        tokens = list(seed_tokens)

        last_known = None
        for i in range(len(tokens) - 1, -1, -1):
            if tokens[i] in self.transitions:
                last_known = tokens[i]
                break

        if last_known is None:
            start = self._weighted_choice(self.starts)
            tokens.append(start)
            last = start
        else:
            last = last_known

        while len(tokens) < max_len:
            options = self.transitions.get(last)
            if not options:
                break
            nxt = self._weighted_choice(options)
            tokens.append(nxt)
            last = nxt
            if len(tokens) >= min_len and nxt in ".,!?":
                break
        return tokens

    def to_dict(self) -> dict:
        return {"transitions": self.transitions, "starts": self.starts}

    @classmethod
    def from_dict(cls, data: dict) -> "MarkovModel":
        return cls(transitions=data.get("transitions", {}), starts=data.get("starts", {}))


class ModelStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> MarkovModel:
        if not os.path.exists(self.path):
            return MarkovModel()
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return MarkovModel.from_dict(data)

    def save(self, model: MarkovModel) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(model.to_dict(), f, ensure_ascii=False)
        os.replace(tmp, self.path)
