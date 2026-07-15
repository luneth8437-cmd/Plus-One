# Opener Adoption Feedback Loop

Goal: use behavioral data - not taste - to iterate the opening assistant.

## Signals (all server-side, no chat text stored)

| Signal | Event / source | Question it answers |
| --- | --- | --- |
| Suggestion sessions | `opener_suggested` | Is the feature discovered? |
| Clicks | `opener_clicked` (index of the chosen card) | Which position/variant gets picked? |
| Adoption | `first_message_sent.opener_usage` = verbatim/edited/none | Do suggestions become real messages? |
| Edit rate | share of `edited` among adopted | Are suggestions close-but-not-quite? |
| Downstream quality | `first_reply_received` rate for suggested vs self-written first messages | Do AI openers actually start conversations better? |

Current scripted baseline (2026-07-14): 46.2% adoption, 50% verbatim.

## Iteration Protocol

1. Ship a prompt change under a bumped `PROMPT_VERSION` (logged into
   `LLMLog.strategy`), so adoption can be split by prompt version.
2. Offline gate first: `evaluate_ai --use-llm` judge scores must not regress
   (judge itself is calibrated against a human rater via `calibrate_judge`).
3. Online read: compare adoption, edit rate, and suggested-vs-self reply rate
   across prompt versions in `funnel_report`.
4. High edit rate on a variant = mine what users changed conceptually
   (length? tone? question vs statement?) and encode it as a style rule -
   as done in prompt v2 (student-tone rules from naturalness 3.89 feedback).

## When to A/B

Not before ~200 first messages per arm (at current scale, months). Until
then: within-user proxy metrics above, honestly labeled. The threshold and
reasoning stay in this doc so the decision is auditable.
