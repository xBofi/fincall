# Model card: FinCall turn classifier

A small multilingual classifier that reads one caller turn and predicts four labels with one
encode: **intent** (continue, repeat, correct, human, hang_up, out_of_scope, question),
**incident category** (the default catalog plus `unclassifiable`), **urgency** (low, medium,
high, emergency) and **work type** (repair, replacement, insurance_claim, neighbour_dispute,
private_unit). It sits in front of the LLM in the call graph (`building_call.nlp.hybrid`) and is
off by default.

## Model

| | |
|---|---|
| Method | SetFit (Tunstall et al., 2022): contrastive fine-tuning of a sentence-transformer, then one logistic-regression head per task |
| Base model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (118M parameters, 50+ languages) |
| Heads | scikit-learn `LogisticRegression(C=4, class_weight="balanced")`, one per task, on the L2-normalised embedding |
| Pair labels for the contrastive step | `cat:<category>` for incidents, `intent:<intent>` for the rest, so the space separates both |
| Size on disk | ~470 MB (body) + < 1 MB (heads); not in git |
| Inference | CPU, one encode per turn; latency measured in `evals/compare_nlp.py` |

### Why SetFit on a small multilingual sentence-transformer

- **Data size.** The project has 46 gold classification rows and 81 gold interpretation rows per
  language, and they are the test set. Training uses 656 hand-written rows (about 30 per
  category, 14 per non-default intent). Fully fine-tuning `xlm-roberta-base` (278M) with a
  softmax head needs thousands of rows to beat a frozen encoder. SetFit is designed for 8 to 64
  examples per class.
- **Languages.** The call flow runs in es, en, ca, ar, hi and ur. The base model is multilingual,
  so one model serves all of them. The training set has es, en and ca, and the encoder carries
  the rest zero-shot.
- **Latency and cost.** It runs on the API server's CPU (production has no GPU). A MiniLM encode
  costs milliseconds and $0, against an LLM call of about 0.6 to 1.5 s.
- **One encode, four answers.** The heads share the embedding, so adding a task costs one tiny
  head, not another model.

### MiniLM vs mpnet (measured)

The same recipe on `paraphrase-multilingual-mpnet-base-v2` (278M), scored on the same gold sets
with the same cached LLM answers. This was measured before the English intent gold grew from 81
to 131 rows; re-run it with `--model-dir` to refresh:

| Base | Size | Train (split run) | Classifier only: intent es / en | category es / en | urgency es / en | Hybrid @ 0.8 | CPU per turn |
|---|---|---|---|---|---|---|---|
| **MiniLM-L12** (chosen) | 466 MB | 225 s | 80.2 % / 75.3 % | 91.3 % / 84.8 % | 69.6 % / 73.9 % | es 81.5 % intent, en 86.0 % | 11 to 13 ms |
| mpnet-base | 1.1 GB | 742 s | 92.6 % / 79.6 % | 89.1 % / 82.6 % | 67.4 % / 73.9 % | identical | 18 to 19 ms |

mpnet is clearly better at intent on its own, but the hybrid results are identical, because
the router only acts on confident labels and the rest goes to the LLM. So the smaller, faster
model wins for this use. If the threshold is lowered later to send fewer turns to the LLM,
re-run this comparison: mpnet's intent head would then matter.

## Data

- **Training set:** `nlp/data/train.jsonl`, built by `nlp/data/build_dataset.py --seed 13`. Hand-written incident
  descriptions and intent phrases in Spanish (main), English and Catalan, each labelled with the
  four tasks. They are augmented with seeded openers ("Hola, …", "Mire, …") and closers
  ("…, desde ayer"). Gold rows are not used.
- **Leakage check:** the builder drops any generated row whose normalised text is ≥ 0.85
  similar (SequenceMatcher) to any `evals/gold_*.jsonl` row. That dropped 10 rows (656 remain).
- **Validation split:** 15 %, stratified by pair label and **grouped by source sentence**, so
  the augmented variants of one sentence never sit on both sides.
- **Test sets:** `evals/gold_classification[_en].jsonl` (category, urgency) and
  `evals/gold_interpretation[_en].jsonl` (intent). Rows without an expected intent count as
  `continue`. Where the gold accepts several answers, any of them counts as a hit.

## Reproduce

```powershell
cd backend
uv sync --extra nlp                       # torch (CUDA 13.2 wheels on Windows), setfit, spaCy
uv run python nlp/data/build_dataset.py --seed 13
uv run python nlp/train_classifier.py     # ~1 min on an RTX 4070 SUPER; writes nlp/models/turn-classifier/
uv run python evals/compare_nlp.py --llm qwen3.5:latest --language es --thresholds 0.6,0.7,0.8,0.9,0.95
uv run python evals/compare_nlp.py --llm qwen3.5:latest --language en --thresholds 0.6,0.7,0.8,0.9,0.95
```

`meta.json` next to the weights stores the config, the SHA-256 of the training data and the
validation scores.

## Results

See the README section "NLP models and evaluation" for the gold-set table (LLM-only vs
classifier-only vs hybrid). Validation (grouped split, 100 rows):

| Task | Accuracy | Macro-F1 |
|---|---|---|
| intent | 91.0 % | 0.821 |
| category | 80.0 % | 0.772 |
| urgency | 55.3 % | 0.583 |
| work type | 52.9 % | 0.427 |

## Limitations and intended use

- **Not a replacement for the LLM.** The classifier cannot extract free data (addresses,
  corrected floors, the question to answer). It only answers a turn when every needed label is
  at or above the threshold and spaCy and the regex find no entity in the turn.
- **Urgency and work type are weak.** They are judgement calls with few examples per class. The
  hybrid router requires all of category, urgency and work type to be confident before it skips
  the LLM, so in practice the weak heads keep most problem descriptions on the LLM.
- **Arabic, Hindi and Urdu** have no training rows and no gold set yet. The encoder is
  multilingual, but nothing here measures those languages. Keep the hybrid off for them until
  gold data exists.
- **Synthetic training data.** Real calls are noisier (STT errors, fillers). Retrain with the
  cases the team marks in the console ("caso para el banco") once there are enough.
- **Safety rules stay in code.** Handing over to a person or hanging up still needs the
  caller's explicit words (`_asks_for_person`, `_says_goodbye`), whatever the classifier says.
