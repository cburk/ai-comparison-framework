"""Deterministic generator for the needle-search corpus.

Creates 12 large lorem-ipsum files with one target fact and several near-miss
distractors hidden among them. Output goes to aicmp/data/needle/ (gitignored).
"""

import random
from pathlib import Path

OUT = Path(__file__).parent / "needle"
N_FILES = 12
WORDS_PER_FILE = 25_000  # ~150KB per file, ~1.8MB total
SEED = 20260919

WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud "
    "exercitation ullamco laboris nisi aliquip ex ea commodo consequat duis aute irure "
    "in reprehenderit voluptate velit esse cillum fugiat nulla pariatur excepteur sint "
    "occaecat cupidatat non proident sunt culpa qui officia deserunt mollit anim id est laborum"
).split()

TARGET_FILE = 7  # 0-based index, file_08.txt
TARGET = (
    "During the spring audit, the Halvorsen Bridge was inspected on 14 March 2024 by "
    "engineer Priya Ramanathan, who recorded a maximum load tolerance of 47 tonnes."
)
DISTRACTORS = {
    2: "The Halvorsen Bridge inspection of 2019 was carried out by engineer Tomas Lindqvist, "
    "who recorded a maximum load tolerance of 31 tonnes.",
    4: "The Halvorson Bridge was inspected on 3 June 2024 by engineer Dana Okafor, who "
    "recorded a maximum load tolerance of 52 tonnes.",
    10: "Repairs on the Halvorsen Bridge in 2024 were supervised by Marcus Bell; no load "
    "tolerance was measured during that work.",
}

QUESTION = (
    "The files in ./corpus are mostly filler text, but a few contain facts about bridge "
    "inspections. Who inspected the Halvorsen Bridge (spelled with an 'e') in 2024, and what "
    "maximum load tolerance did they record? Answer in one sentence."
)
ANSWER = "Priya Ramanathan|47 tonnes"


def _paragraphs(rng: random.Random) -> list[str]:
    paras, remaining = [], WORDS_PER_FILE
    while remaining > 0:
        n = min(remaining, rng.randint(80, 200))
        words = [rng.choice(WORDS) for _ in range(n)]
        paras.append((" ".join(words)).capitalize() + ".")
        remaining -= n
    return paras


def generate(force: bool = False) -> Path:
    if OUT.exists() and not force and len(list(OUT.glob("file_*.txt"))) == N_FILES:
        return OUT
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    for i in range(N_FILES):
        paras = _paragraphs(rng)
        planted = TARGET if i == TARGET_FILE else DISTRACTORS.get(i)
        if planted:
            pos = rng.randint(len(paras) // 4, 3 * len(paras) // 4)
            paras[pos] = paras[pos] + " " + planted
        (OUT / f"file_{i + 1:02d}.txt").write_text("\n\n".join(paras) + "\n")
    return OUT


if __name__ == "__main__":
    print(generate(force=True))
