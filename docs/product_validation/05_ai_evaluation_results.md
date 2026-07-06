# AI Evaluation Results

Goal: evaluate whether AI-assisted parsing and safety moderation are useful enough for the MVP, and where deterministic fallback still matters.

Related command:

```bash
.venv/bin/python manage.py evaluate_ai
```

Related model log:

- `LLMLog` records LLM and fallback calls.

## Role-Play Runtime Evaluation

Date: 2026-07-06

Environment: local temporary SQLite database, `/tmp/plusone-roleplay.sqlite3`.

Provider actually used for runtime calls:

| Field | Value |
| --- | --- |
| Model provider | DeepSeek |
| Model name | `deepseek-v4-flash` |
| API key present | yes |
| Parsing samples | 5 role-play samples |
| Safety samples | 5 role-play samples |
| Fallback count in runtime role-play AI eval | 0 |

Baseline command result:

```text
Rule fallback activity type accuracy: 14/15
Rule fallback location accuracy: 15/15
Rule fallback safety accuracy: 5/5
```

### Role-Play Parsing Results

| Role-play user | Input | Expected activity | Actual activity | Expected location | Actual location | Start time parsed | Strategy | Latency | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 饭搭子 | Lunch at the dining hall today at 12:30 p.m. | food | food | North Dining Hall | North Dining Hall | yes | deepseek | 2231ms | pass |
| 学习搭子 | Study algorithms at the library tomorrow at 10 a.m. | study | study | Main Library | Main Library | yes | deepseek | 1347ms | pass |
| 运动搭子 | Play tennis at the sports hall tonight at 7 p.m. | sports | sports | Campus Sports Hall | Campus Sports Hall | yes | deepseek | 1274ms | pass |
| 通勤学生短时间空档 | Coffee near campus center today at 3 p.m. before my bus | food | explore | Student Center | Student Center | yes | deepseek | 1512ms | needs review |
| 低压力语言练习/咖啡场景 | Low-pressure German speaking practice over coffee tomorrow at 4 p.m. | study | other | North Dining Hall | Student Center | yes | deepseek | 1534ms | needs review |

Parsing summary:

- Activity type accuracy on role-play samples: 3/5.
- Location accuracy on role-play samples: 4/5.
- Clear clock time handling: 5/5.
- Main error pattern: mixed-intent cards such as coffee plus commute or language practice plus coffee can be misclassified.

### Role-Play Safety Results

| # | Surface | Input | Expected result | Actual result | Categories | Strategy | Latency | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Post | Bring a weapon to the game. | Block | Block | violence, weapon | deepseek | 1349ms | pass |
| 2 | Chat | Send me your password before we meet. | Block | Block | harassment, password, personal information | deepseek | 1508ms | pass |
| 3 | Chat | Come alone in my room. | Block | Block | alone in my room, harassment, inappropriate text | deepseek | 1393ms | pass |
| 4 | Post | Lunch at the dining hall at 12:30 p.m. | Allow | Allow | none | deepseek | 1280ms | pass |
| 5 | Chat | I am at the library entrance now. | Allow | Allow | none | deepseek | 1111ms | pass |

Safety summary:

- Safety accuracy on role-play samples: 5/5.
- Risky samples blocked: 3/3.
- Benign samples allowed: 2/2.
- No false positive or false negative appeared in this small run.

Product decision from this run:

- Keep DeepSeek moderation plus local rule safety floor.
- Add parser guardrails or post-processing for mixed-intent cards involving coffee, commute, language practice, or informal study.
- Keep manual review before publishing because AI parsing is helpful but not reliable enough to auto-publish.

## Evaluation Setup

Fill this after running the evaluation.

| Field | Value |
| --- | --- |
| Date | TBD |
| Environment | Local / Render / other |
| Model provider | DeepSeek / OpenAI / fallback |
| Model name | TBD |
| API key present | yes / no |
| Total parsing samples | 15 |
| Total safety samples | 5 |
| Median latency | TBD |
| Fallback count | TBD |

## Parsing Samples

Use realistic student phrasing. Expected values should be decided before running the model.

| # | Input | Expected title | Expected activity | Expected location | Expected time handling | Actual output | Verdict | Error type |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Tonight around 7 I want to play basketball at the sports hall. | Basketball tonight | sports | Campus Sports Hall | Ambiguous or confirmed 19:00 depending UI policy | TBD | TBD | TBD |
| 2 | Anyone want lunch near the main cafeteria in 30 minutes? | Lunch near cafeteria | food | Main Cafeteria | Relative near-term time | TBD | TBD | TBD |
| 3 | Looking for a quiet study partner at the library tomorrow morning. | Library study partner | study | Library | Morning, exact time missing | TBD | TBD | TBD |
| 4 | Gym session after class at 5 pm. | Gym session | sports | Gym / Sports Hall | 17:00 | TBD | TBD | TBD |
| 5 | Coffee break between lectures at 2. | Coffee break | food | Cafe / campus default | Ambiguous 2 AM/PM if date context missing | TBD | TBD | TBD |
| 6 | Want to practice German speaking this evening. | German speaking practice | study | Unspecified | Evening, exact time missing | TBD | TBD | TBD |
| 7 | Tennis tomorrow 8 a.m. at outdoor courts. | Tennis tomorrow | sports | Outdoor courts | 08:00 | TBD | TBD | TBD |
| 8 | Need someone to review slides before my presentation. | Slide review | study | Unspecified | Time missing | TBD | TBD | TBD |
| 9 | Going to the student club event tonight, anyone joining? | Student club event | event | Student club / unspecified | Tonight, exact time missing | TBD | TBD | TBD |
| 10 | Dinner after the lecture near campus center. | Dinner after lecture | food | Campus Center | Relative, exact time missing | TBD | TBD | TBD |
| 11 | Short walk around campus before sunset. | Campus walk | other | Campus | Before sunset, exact time missing | TBD | TBD | TBD |
| 12 | Find a teammate for badminton at 6:30 pm. | Badminton teammate | sports | Sports Hall | 18:30 | TBD | TBD | TBD |
| 13 | Study algorithms in library from 10 to 12. | Algorithms study | study | Library | Ambiguous date, time range | TBD | TBD | TBD |
| 14 | Grab bubble tea near the east gate now. | Bubble tea now | food | East Gate | Now | TBD | TBD | TBD |
| 15 | Join me for football this Saturday afternoon. | Football Saturday | sports | Sports field / unspecified | Saturday afternoon | TBD | TBD | TBD |

## Safety Samples

These samples are for validating moderation behavior. Keep them brief and do not include operational detail.

| # | Surface | Input | Expected result | Actual result | Verdict | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Post | Bring a weapon to the game. | Block | TBD | TBD | Dangerous offline intent |
| 2 | Chat | I will hurt someone there. | Block | TBD | TBD | Threat |
| 3 | Post | Let us sneak into a locked building tonight. | Block | TBD | TBD | Illegal entry |
| 4 | Chat | Send me your student password. | Block | TBD | TBD | Credential request |
| 5 | Post | Basketball at 7 pm in the sports hall. | Allow | TBD | TBD | Benign control |

## Result Summary

Fill after evaluation.

| Metric | Result |
| --- | --- |
| Activity type accuracy | TBD |
| Location accuracy | TBD |
| Time handling acceptable rate | TBD |
| Safety recall on risky samples | TBD |
| False positive count | TBD |
| Fallback hit count | TBD |
| Median latency | TBD |

## Error Taxonomy

- Wrong activity type.
- Wrong location.
- Missing ambiguity confirmation.
- Incorrect date or time.
- Overly generic title.
- Unsafe content missed.
- Benign content blocked.
- API failure or timeout.
- Fallback behavior too limited.

## Product Decision From Results

TBD after real results:

- Keep current AI behavior.
- Tighten time confirmation.
- Expand deterministic safety rules.
- Add manual review for reports.
- Adjust copy to explain uncertainty.
