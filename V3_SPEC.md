# Soccer Goal Predictor V3 — Complete Product, Prediction Engine, Data, UX and Infrastructure Upgrade

## ROLE

You are the senior full-stack engineer, quantitative football modeller, data engineer, UI/UX engineer, DevOps engineer, QA engineer, and product architect responsible for upgrading the existing **Soccer Goal Predictor** application.

You are working on an existing production application.

**Do not rebuild the project from scratch.**

First inspect the existing repository, architecture, database schema, API routes, React components, CSS, background jobs, prediction engine, ingestion services, tests, environment configuration, and deployment configuration.

Preserve working functionality unless there is a demonstrable reason to replace it.

The goal is to turn the existing application into a **production-grade football probability and goal-analysis platform**.

The system must remain transparent, auditable, statistically defensible, fast, mobile-friendly, and capable of scanning all available fixtures across all supported competitions.

---

# 1. CORE PRODUCT OBJECTIVE

Transform the application from:

> "A website showing football predictions"

into:

> **A football probability intelligence platform that analyses every available fixture, estimates goal probabilities, identifies statistically strong goal opportunities, compares model probabilities with market prices where available, records predictions before kickoff, and objectively measures model performance afterward.**

The application must never imply certainty.

Do NOT use:

* guaranteed
* sure win
* banker
* cannot lose
* fixed
* certain bet
* guaranteed goals

Use:

* model probability
* estimated probability
* statistical edge
* expected value
* high-confidence model signal
* goal potential
* model agreement
* calibrated probability

---

# 2. FIRST TASK — FULL CODEBASE AUDIT

Before changing code:

1. Inspect the complete repository.
2. Identify frontend entry points.
3. Identify all backend services.
4. Identify prediction/model code.
5. Identify database models.
6. Identify ingestion pipelines.
7. Identify APScheduler jobs.
8. Identify API routes.
9. Identify external data providers.
10. Identify Render deployment configuration.
11. Identify all environment variables.
12. Identify current test coverage.
13. Identify dead code.
14. Identify duplicated logic.
15. Identify hard-coded values.
16. Identify potential race conditions.
17. Identify SQLite concurrency risks.
18. Identify prediction/data leakage risks.
19. Identify frontend performance problems.
20. Identify mobile/responsive problems.

Create an internal implementation plan before making changes.

Do not remove existing working functionality merely because a cleaner implementation is possible.

---

# 3. TARGET ARCHITECTURE

Move toward the following architecture while retaining SQLite initially if migration would create unnecessary production risk:

```text
React + Vite
      |
      v
FastAPI REST API
      |
      +----------------------+
      |                      |
      v                      v
 PostgreSQL*              Redis*
      |                      |
      +----------+-----------+
                 |
                 v
          Prediction Engine
                 |
       +---------+---------+
       |         |         |
       v         v         v
 Dixon-Coles   Elo    Hierarchical
   Model       Model    Poisson
       |         |         |
       +---------+---------+
                 |
                 v
          Ensemble Layer
                 |
                 v
       Calibration Layer
                 |
                 v
      Goal Probability Engine
                 |
                 v
      Prediction Snapshot DB
```

`*` PostgreSQL and Redis may be introduced incrementally.

Do not force a database migration before verifying the current application's operational requirements.

---

# 4. PREDICTION ENGINE — DO NOT USE A SINGLE MODEL

The primary prediction architecture must become an ensemble.

Implement these components as separate model classes/services.

## MODEL A — Dixon-Coles

Retain the current Dixon-Coles implementation but improve it.

It must estimate:

* home attack strength
* away attack strength
* home defensive strength
* away defensive strength
* home advantage
* league scoring environment
* low-score correlation parameter rho
* time-decay parameter

The model must generate:

* home expected goals
* away expected goals
* total expected goals
* exact scoreline probability matrix
* 1X2
* Over 0.5
* Over 1.5
* Over 2.5
* Over 3.5
* Over 4.5
* BTTS
* BTTS Yes/No
* first-half goal probabilities
* second-half goal probabilities

Dixon-Coles specifically corrects low-scoring outcomes, so retain it as the core score-distribution model rather than discarding it.

---

# 5. MODEL B — DYNAMIC ELO

Retain the Elo system but upgrade it.

Implement:

* chronological replay
* home advantage
* competition weighting
* recency weighting
* margin-of-victory adjustment where statistically justified
* promotion/relegation handling
* newly promoted team initialization
* newly formed team handling
* neutral venue handling

Store:

```text
team_id
rating
rating_date
competition
home_advantage
rating_change
model_version
```

Never calculate historical predictions using future Elo information.

---

# 6. MODEL C — HIERARCHICAL POISSON

Implement a hierarchical goal model where practical.

Purpose:

* share information across teams
* reduce overfitting for teams with limited historical data
* stabilize ratings for smaller competitions
* provide a complementary forecast to Dixon-Coles

The model should estimate:

```text
home_goal_rate
away_goal_rate
league_baseline
team_attack
team_defence
home_advantage
```

Use regularization/shrinkage.

If the existing Python environment cannot support a suitable Bayesian library, implement a regularized hierarchical approximation rather than introducing an unnecessarily fragile dependency.

---

# 7. MODEL D — MACHINE LEARNING GOAL MODEL

Add a supervised ML model only after the statistical models are functioning correctly.

Preferred starting model:

**Gradient-boosted trees**

Use a mature implementation such as:

* LightGBM
* XGBoost
* HistGradientBoosting

Choose the dependency that is most compatible with the existing deployment environment.

Do not use a neural network simply because it sounds more advanced.

The model should predict:

### Target A

Home goals

### Target B

Away goals

and/or:

### Target C

Probability of goal markets

Features can include:

* team attack strength
* team defensive strength
* Elo
* rolling goals scored
* rolling goals conceded
* rolling xG
* rolling xGA
* shots
* shots on target
* home/away split
* rest days
* fixture congestion
* league scoring rate
* recent form
* injuries
* suspensions
* confirmed lineups when available
* goalkeeper availability
* market information when explicitly permitted
* weather where available
* historical team strength

Every feature must be timestamped.

No future information may enter a historical prediction.

---

# 8. ENSEMBLE MODEL

Do not simply average the models with arbitrary weights.

Implement learned ensemble weights using historical walk-forward validation.

Candidate ensemble:

```text
Dixon-Coles
      +
Dynamic Elo
      +
Hierarchical Poisson
      +
Gradient Boosting
      |
      v
Weighted Ensemble
      |
      v
Probability Calibration
      |
      v
Final Probability
```

Weights must be learned exclusively from training/validation periods.

For example:

```text
Dixon-Coles:          35%
Hierarchical Poisson: 25%
Elo:                  15%
ML Goal Model:        25%
```

These numbers are placeholders only.

DO NOT hard-code them as the final weights.

Optimize them through temporal validation.

---

# 9. MARKET-AWARE ENSEMBLE — OPTIONAL BUT IMPORTANT

If reliable bookmaker odds are available, create a separate market probability input.

Do not blindly treat bookmaker odds as truth.

Remove bookmaker margin/overround.

Store:

```text
bookmaker
market
selection
decimal_odds
implied_probability
fair_probability
timestamp
source
```

Then evaluate:

```text
Model probability
Market probability
Difference
Expected value
```

The market should be treated as a strong benchmark, not automatically as the winner.

Recent research illustrates why this matters: a calibrated structural model can still underperform the closing market, so model quality must be demonstrated out-of-sample rather than assumed.

---

# 10. CALIBRATION LAYER — MANDATORY

Every published probability must pass through a calibration layer.

Evaluate:

* isotonic regression
* Platt/logistic calibration
* beta calibration
* temperature scaling where appropriate

Select the calibration method through temporal validation.

The final output must be:

```text
Raw probability
        |
        v
Calibration
        |
        v
Published probability
```

The objective is:

> When the system says 80%, the event should occur approximately 80% of the time over a sufficiently large sample.

This is more important than maximizing simple hit rate.

---

# 11. WALK-FORWARD VALIDATION — MANDATORY

Never randomly split football matches into train/test sets.

Use chronological walk-forward validation.

Example:

```text
Train: 2019–2022
Validate: 2023

Train: 2019–2023
Validate: 2024

Train: 2019–2024
Validate: 2025

Train: 2019–2025
Validate: 2026
```

For every prediction:

```text
prediction_timestamp < all information used by model
```

No leakage.

No future results.

No future team ratings.

No future form.

No future injuries.

No future odds.

No post-match information.

---

# 12. MODEL EVALUATION

Implement a permanent model evaluation framework.

Track:

## Classification metrics

* accuracy
* precision
* recall
* ROC-AUC where appropriate

## Probability metrics

* Brier score
* log loss
* calibration error
* reliability curves

## Multi-class metrics

* multiclass Brier score
* log loss
* ranked probability score

## Betting/market metrics

Only where legitimate historical odds exist:

* ROI
* yield
* maximum drawdown
* closing-line value
* expected value
* hit rate

Do NOT display ROI unless odds were actually available at prediction time.

---

# 13. CALIBRATION DASHBOARD

Create:

## Model Performance

Show:

```text
Predictions
18,421

Brier Score
0.143

Log Loss
0.421

Calibration
97%

Over 1.5
84.2%

Over 2.5
59.7%

BTTS
56.1%
```

Add a calibration graph:

```text
Predicted probability
vs
Actual frequency
```

Create probability buckets:

```text
50–55%
55–60%
60–65%
65–70%
70–75%
75–80%
80–85%
85–90%
90%+
```

Show predicted vs actual frequency.

---

# 14. IMMUTABLE PREDICTION SNAPSHOTS

Every prediction published before kickoff must create an immutable snapshot.

Store:

```text
prediction_id
fixture_id
timestamp
model_version
ensemble_version
home_xg
away_xg
total_xg
over_0_5
over_1_5
over_2_5
over_3_5
over_4_5
btts
home_win
draw
away_win
score_matrix
elo_home
elo_away
form_snapshot
odds_snapshot
data_version
```

After kickoff, the original prediction must never be overwritten.

If the model updates, create a new version/snapshot.

---

# 15. MODEL VERSIONING

Every prediction must identify:

```text
Model version: v2.0.0
Ensemble version: e2.1
Calibration version: c1.3
Data snapshot: ds20260818
```

When the model changes, historical predictions must remain linked to the old version.

---

# 16. "WHY THIS PREDICTION?" FEATURE

Every match must have an explanation panel.

Example:

```text
WHY OVER 1.5 = 87%

Combined xG
3.12

Home attack
Strong

Away defensive trend
Weak

Recent combined goal rate
3.4

Elo difference
+176

Recent xG trend
Positive

Model agreement
4/4 models bullish
```

Do not fabricate explanations.

Only show factors actually used by the model.

---

# 17. MODEL AGREEMENT SCORE

Create:

```text
MODEL AGREEMENT

Dixon-Coles       85%
Hierarchical      88%
Elo-derived       82%
ML Model          90%

Ensemble          87%

Agreement:
HIGH
```

This is useful for distinguishing:

### Strong probability + high model agreement

from:

### Strong probability + models disagreeing

---

# 18. GOAL OPPORTUNITY SCANNER

This is one of the most important new features.

The application must scan **ALL AVAILABLE FIXTURES ACROSS ALL SUPPORTED LEAGUES AND COMPETITIONS**.

Do not only show major leagues.

Do not hard-code a list of competitions.

The scanner should dynamically query all competitions for which valid fixture and team data are available.

For every fixture calculate:

```text
Over 0.5 probability
Over 1.5 probability
Over 2.5 probability
Over 3.5 probability
BTTS probability
Combined xG
Home xG
Away xG
First-half O0.5
Second-half O0.5
Model agreement
Market edge where odds exist
Data quality score
```

---

# 19. GOAL POTENTIAL SCORE

Create a new composite ranking called:

## Goal Potential Score

Do NOT make this an arbitrary percentage.

Construct it from validated components such as:

* calibrated Over 1.5 probability
* calibrated Over 2.5 probability
* combined xG
* BTTS probability
* recent goal environment
* model agreement
* data quality

Learn or validate the weights using historical data.

Example output:

```text
Goal Potential
92/100
```

But clearly distinguish:

```text
Goal Potential Score: 92
Over 1.5 probability: 91%
Over 2.5 probability: 68%
Combined xG: 3.41
```

Do not imply that "92/100" means 92% probability.

---

# 20. UNIVERSAL GOAL SCANNER UI

Create a new primary navigation section:

# Goal Scanner

Tabs:

```text
ALL
OVER 1.5
OVER 2.5
BTTS
HIGH xG
VALUE
LIVE
```

Filters:

* date
* country
* competition
* minimum probability
* minimum xG
* model agreement
* value only
* live/upcoming
* kickoff time

---

# 21. GOAL SCANNER DEFAULT SORT

Default sort should be:

1. strongest calibrated goal probability
2. model agreement
3. expected goals
4. value/EV if market odds exist

Do not rank solely by raw probability.

---

# 22. EXAMPLE GOAL SCANNER CARD

Create cards similar to:

```text
────────────────────────────────────

ENGLISH PREMIER LEAGUE

Arsenal vs Chelsea
19:30

Combined xG
3.21

OVER 1.5
88%

OVER 2.5
67%

BTTS
61%

MODEL AGREEMENT
4 / 4

GOAL POTENTIAL
HIGH

VALUE
+6.8pp

[ ANALYSE MATCH ]

────────────────────────────────────
```

---

# 23. ALL-LEAGUE COVERAGE

The backend must expose:

```text
GET /api/v1/goal-scanner
```

Parameters:

```text
date
competition
country
market
min_probability
min_xg
min_model_agreement
value_only
live_only
limit
offset
```

Return all qualifying fixtures across all available competitions.

Implement pagination.

Do not load thousands of fixtures into the browser at once.

---

# 24. "ALL MATCHES WITH GOAL POTENTIAL"

Create a user-friendly page:

# Today's Goal Opportunities

Sections:

### Exceptional Goal Potential

Model probability >= configured validated threshold.

### Strong Goal Potential

Second tier.

### Interesting Goal Potential

Worth monitoring.

### All Matches

Complete fixture list.

Do not arbitrarily call a match "safe."

---

# 25. THRESHOLDS MUST BE CONFIGURABLE

Create admin/model configuration for:

```text
Over 1.5 minimum probability
Over 2.5 minimum probability
BTTS minimum probability
Minimum combined xG
Minimum model agreement
Minimum data quality
Minimum historical sample
Minimum market edge
```

Do not bury thresholds inside React components.

Store them in configuration.

---

# 26. DATA QUALITY SCORE

Every prediction should have a data-quality assessment.

Example:

```text
DATA QUALITY
94/100

✓ Recent results available
✓ Team ratings current
✓ Competition data complete
✓ Lineups available
✓ Odds updated
✓ Sufficient historical sample
```

If critical information is missing:

```text
DATA QUALITY
61/100

⚠ Limited historical data
⚠ No confirmed lineup
⚠ Market odds unavailable
```

Do not present weak-data predictions with the same visual confidence as high-quality predictions.

---

# 27. LINEUPS AND PLAYER AVAILABILITY

Where the data provider permits it, integrate:

* injuries
* suspensions
* expected lineups
* confirmed lineups
* goalkeeper availability
* key attacker availability

When a confirmed lineup becomes available:

1. ingest it
2. recompute relevant model features
3. generate a new prediction snapshot
4. show what changed

Example:

```text
PREDICTION UPDATED

Over 1.5
84% → 89%

Reason:
Starting striker confirmed
```

Only show reasons backed by actual model features.

---

# 28. LIVE MATCH MODEL

Separate pre-match and live models.

Do not modify a pre-match prediction silently.

For live matches show:

```text
LIVE 73'

1–0

Current xG
1.72 – 0.61

Remaining expected goals
0.48

Probability of another goal
61%

Live Over 1.5
93%

Live Over 2.5
47%
```

Clearly label:

```text
PRE-MATCH
LIVE
```

---

# 29. PREDICTION CHANGE HISTORY

For every fixture:

```text
14:00
Over 1.5 = 82%

16:30
Over 1.5 = 85%

17:45
Over 1.5 = 89%

18:00
Lineup confirmed

18:30
Kickoff
```

Users should be able to see what changed and why.

---

# 30. EXACT SCORE VISUALIZATION

Replace a simple score list with a probability heatmap.

Display:

```text
             AWAY GOALS
           0     1     2     3     4+

HOME 0    5.1   8.3   6.7   3.2   1.4
     1    8.9  13.4  10.7   5.1   2.0
     2    7.4  11.2  12.8   6.4   2.7
     3    4.0   6.2   7.1   4.5   2.1
     4+   1.8   3.0   3.5   2.6   1.5
```

Highlight the highest-probability scorelines.

---

# 31. VALUE BET ENGINE

Replace the simplistic:

```text
model probability > implied probability + 4%
```

with:

```text
fair implied probability
model probability
probability edge
expected value
```

Formula:

```text
implied_probability = 1 / decimal_odds

EV = model_probability * decimal_odds - 1
```

Example:

```text
Model probability
82%

Odds
1.40

Implied probability
71.43%

Probability edge
+10.57pp

Expected value
+14.8%
```

Only display EV where valid timestamped odds exist.

---

# 32. ODDS PROVENANCE

Every displayed market price must show:

```text
Bookmaker
Market
Odds
Timestamp
Source
```

Example:

```text
Best available:
1.40

Updated:
18 Aug 2026 18:21 GMT+1
```

If odds are stale, display:

```text
STALE ODDS
```

Do not calculate current-looking value from old odds.

---

# 33. SMART ACCUMULATOR REWORK

Keep Smart Accas but remove misleading language.

Rename:

```text
Safe Double
```

to:

```text
Conservative 2-Fold
```

Rename:

```text
Balanced 5-Fold
```

to:

```text
Balanced 5-Fold
```

Rename:

```text
High Yield 8-Fold
```

to:

```text
High Variance 8-Fold
```

For every accumulator show:

```text
Selections
Combined model probability
Fair combined odds
Bookmaker odds
Expected value
```

For independent approximation:

```text
combined_probability =
p1 × p2 × p3 × ... × pn
```

But explicitly account for correlation where appropriate.

Do not claim selections are independent if they are not.

---

# 34. ACCUMULATOR EXAMPLE

Display:

```text
SMART ACCA

5 selections

Selection probabilities:

86%
84%
82%
81%
79%

Estimated combined probability:
37%

Fair combined odds:
2.70

Bookmaker:
3.10

Model EV:
+14.8%
```

Clearly label accumulator probability as an estimate.

---

# 35. FINISHED MATCHES

Upgrade the Finished Matches section.

Each completed prediction should show:

```text
Prediction issued:
18 Aug 2026 14:00

Prediction:
Over 1.5 — 82.4%

Actual:
3–1

Result:
HIT

Model version:
v2.0.0
```

Never retroactively change the original probability.

---

# 36. MODEL TRACK RECORD

Create:

# Model Track Record

Filters:

* market
* competition
* probability range
* date
* model version

Metrics:

```text
Total predictions
Correct predictions
Hit rate
Brier score
Log loss
Calibration
ROI where odds exist
Maximum drawdown
```

---

# 37. MODEL PERFORMANCE BY LEAGUE

Create:

```text
Premier League
Predictions: 1,240
O1.5 calibration: 96%
Brier: 0.142

La Liga
Predictions: 1,198
O1.5 calibration: 98%
Brier: 0.139

Bundesliga
Predictions: 1,090
O1.5 calibration: 94%
Brier: 0.151
```

Only show statistics with sufficiently large samples.

---

# 38. MODEL PERFORMANCE BY PROBABILITY

Create:

```text
Probability     Predicted     Actual

50–55%          52.5%         51.8%
55–60%          57.5%         58.1%
60–65%          62.5%         61.9%
65–70%          67.5%         68.0%
70–75%          72.5%         72.1%
75–80%          77.5%         77.9%
80–85%          82.5%         83.1%
85–90%          87.5%         86.9%
90%+            92.0%         91.4%
```

This is one of the most important trust-building features.

---

# 39. MOBILE-FIRST REDESIGN

The application must work excellently on:

* iPhone
* Android
* small screens
* tablets
* desktop

On mobile:

Do not require horizontal scrolling for the primary prediction information.

Use:

```text
Team
vs
Team

O1.5 88%
O2.5 67%
BTTS 61%

xG 3.21

[Analyse]
```

Move secondary information into expandable sections.

---

# 40. DESKTOP DASHBOARD

Desktop can expose more information:

```text
Goal Scanner
Filters
League
Probability
xG
BTTS
Value
Model agreement
```

Maintain strong visual hierarchy.

---

# 41. REMOVE INFORMATION OVERLOAD

Prioritize:

1. Goal probability
2. xG
3. model agreement
4. value
5. prediction explanation
6. supporting statistics

Secondary information:

* H2H
* weather
* historical details

should not dominate the screen.

---

# 42. H2H

Keep H2H.

But label it:

> Historical Context

Do not imply that H2H is necessarily a strong predictive feature.

If it is not included in the current model, explicitly say:

```text
Contextual information — not directly used in model probability.
```

---

# 43. FORM

Improve form from:

```text
W W L W W
```

to:

```text
Last 5

Goals:
9–4

xG:
8.7–4.9

Shots:
72

O1.5:
4/5
```

Where the data exists.

---

# 44. LEAGUE-SPECIFIC PARAMETERS

Do not assume one universal football scoring environment.

For each competition where sufficient data exists, estimate:

* baseline home goals
* baseline away goals
* home advantage
* Dixon-Coles rho
* temporal decay
* scoring variance

Use hierarchical fallback for leagues with insufficient sample sizes.

Fallback hierarchy:

```text
Team + league
↓
League
↓
Regional competition group
↓
Global football prior
```

Never create unstable league parameters from tiny samples.

---

# 45. NEW TEAM HANDLING

If a team has insufficient historical matches:

Use:

* league prior
* promoted-team prior
* Elo prior
* roster information where available

Reduce confidence.

Never produce a highly confident prediction from insufficient data.

---

# 46. DATA INGESTION

Retain multiple data sources where possible.

Create a canonical fixture mapping system.

Each fixture should have:

```text
internal_fixture_id
provider
provider_fixture_id
competition
season
home_team
away_team
kickoff
status
```

Implement deduplication.

Do not create duplicate matches when ESPN and Football-Data represent the same fixture.

---

# 47. DATA QUALITY VALIDATION

Before accepting data:

Check:

* duplicate fixture
* impossible score
* invalid date
* missing team
* invalid competition
* impossible kickoff state
* corrupted odds
* stale odds
* abnormal xG
* duplicate team mapping

Flag anomalies rather than silently ingesting them.

---

# 48. SCHEDULER

Separate these jobs logically:

### Live job

30–60 second cadence.

Only update genuinely live data.

### Fixture ingestion

Periodic.

### Historical/statistics refresh

Periodic.

### Model retraining

Separate scheduled job.

### Prediction generation

Separate scheduled job.

### Telegram broadcast

Separate job.

Do not let a failed Telegram message break prediction generation.

Do not let a slow external data provider block the API.

---

# 49. BACKGROUND WORKERS

Where practical, move expensive work away from the FastAPI request process.

Do not calculate an entire league model during a user's HTTP request.

API requests should retrieve already-computed results.

---

# 50. DATABASE

SQLite may remain temporarily.

However, design the ORM/schema so migration to PostgreSQL is straightforward.

If production traffic or concurrent background jobs justify it, migrate to PostgreSQL.

Preserve:

* SQLAlchemy
* migrations
* relationships
* indexes
* transactional integrity

Add indexes for:

```text
fixture.kickoff
fixture.status
fixture.competition_id
prediction.fixture_id
prediction.created_at
prediction.market
historical_result.team_id
elo_rating.team_id
```

---

# 51. REDIS/CACHE

If infrastructure allows it, add Redis for:

* fixture cache
* prediction cache
* API response cache
* live match state
* rate limiting
* background task coordination

Do not use Redis as the authoritative historical database.

---

# 52. API VERSIONING

Create:

```text
/api/v1/
```

Examples:

```text
GET /api/v1/fixtures
GET /api/v1/fixtures/live
GET /api/v1/predictions
GET /api/v1/predictions/{fixture_id}
GET /api/v1/goal-scanner
GET /api/v1/model/performance
GET /api/v1/model/calibration
GET /api/v1/model/versions
GET /api/v1/competitions
```

Use consistent JSON response schemas.

---

# 53. API HEALTH

Create:

```text
GET /api/v1/health
```

Return:

```json
{
  "status": "healthy",
  "database": "healthy",
  "data_ingestion": "healthy",
  "prediction_engine": "healthy",
  "last_fixture_update": "...",
  "last_model_update": "...",
  "version": "2.0.0"
}
```

Do not expose secrets.

---

# 54. FRONTEND CONNECTION STATES

Replace a simplistic:

```text
LIVE
OFFLINE
```

with:

```text
CONNECTED — 82ms
CONNECTING
API WAKING
DEGRADED
OFFLINE
```

If Render is waking the backend, make that visible.

Do not falsely report a healthy connection.

---

# 55. DATA FRESHNESS

Display:

```text
Fixtures updated:
2m ago

Live scores:
18s ago

Model:
47m ago

Odds:
38s ago

Weather:
11m ago
```

Users should know how fresh the information is.

---

# 56. "WHAT CHANGED?" COMPONENT

If prediction changes materially:

```text
Prediction changed

Over 1.5
84% → 89%

Changes:
+ Confirmed lineup
+ Updated team news
+ New market price
```

Only show real causes.

---

# 57. TRANSPARENCY PAGE

Create:

# How Our Model Works

Sections:

1. Data sources
2. Dixon-Coles
3. Elo
4. Hierarchical Poisson
5. Machine learning model
6. Ensemble
7. Calibration
8. Goal probability calculation
9. Value calculation
10. Backtesting
11. Model limitations
12. Prediction history

Explain that probabilities are estimates, not guarantees.

Dixon-Coles should be described accurately as a statistical score model, not marketed as magical AI. The original Dixon-Coles work was specifically based on Poisson regression and dynamic team performance.

---

# 58. "WHY NOT JUST USE AI?"

Add a transparent explanation:

> Football prediction is fundamentally a probability problem. Our system combines statistical goal models, team-strength ratings and machine learning rather than relying on a single black-box model.

This is more credible.

---

# 59. MODEL LIMITATIONS

Explicitly state:

* football has high randomness
* red cards can radically alter outcomes
* lineups can change late
* injuries can be uncertain
* data may be incomplete
* bookmaker markets can be highly efficient
* probabilities are not guarantees

---

# 60. TESTING

Add unit tests for:

### Prediction mathematics

* Poisson PMF
* Dixon-Coles correction
* probability matrix sums to 1
* Over 1.5 calculation
* Over 2.5 calculation
* BTTS
* exact score probabilities
* EV

### Data

* deduplication
* fixture matching
* timezone conversion
* historical snapshots

### Model

* no future leakage
* chronological training
* deterministic inference
* model versioning

### API

* fixtures
* goal scanner
* prediction endpoint
* performance endpoint

### Frontend

* filters
* search
* date selection
* modal
* responsive layout
* loading/error states

---

# 61. PROBABILITY SANITY TESTS

Every generated probability must satisfy:

```text
0 <= p <= 1
```

and the scoreline matrix must approximately satisfy:

```text
sum(score_probabilities) = 1
```

within numerical tolerance.

Also verify:

```text
P(O1.5) + P(U1.5) = 1
P(BTTS Yes) + P(BTTS No) = 1
P(Home) + P(Draw) + P(Away) = 1
```

---

# 62. PERFORMANCE

The frontend must not recalculate thousands of predictions.

The backend should precompute.

Use:

* pagination
* caching
* memoization
* lazy loading
* virtualized lists where necessary

Goal Scanner should remain responsive with thousands of fixtures.

---

# 63. SEO

Create useful public pages where appropriate:

```text
/today
/goal-scanner
/competition/premier-league
/competition/la-liga
/match/{slug}
/methodology
/model-performance
```

Use server-compatible metadata/prerendering where necessary.

Do not create thin duplicate pages for every fixture.

---

# 64. TELEGRAM

Upgrade Telegram output.

Example:

```text
⚽ TODAY'S GOAL SCANNER

🔥 Arsenal vs Chelsea
O1.5 — 88%
O2.5 — 67%
xG — 3.21
Model agreement — 4/4

🔥 Team A vs Team B
O1.5 — 86%
...

View full analysis:
[website]
```

Do not advertise certainty.

Include model timestamp.

---

# 65. PREDICTION REPORTING

Daily report:

```text
MODEL DAILY REPORT

Predictions:
47

Over 1.5:
41/47

Hit rate:
87.2%

Brier:
0.141

Best probability bucket:
80–85%

Model calibration:
96%
```

Do not cherry-pick only winning selections.

---

# 66. ADMIN DASHBOARD

Create internal/admin pages for:

* data ingestion status
* provider health
* fixture counts
* model version
* last training
* prediction counts
* calibration
* failed jobs
* database health
* stale odds
* stale fixtures
* model drift

---

# 67. MODEL DRIFT

Implement monitoring for:

* league goal-rate changes
* probability calibration deterioration
* changing model error
* data-source distribution shifts
* team rating instability

Trigger warnings:

```text
MODEL DRIFT DETECTED

Bundesliga O1.5 calibration
96% → 89%

Recommended:
Retrain/recalibrate
```

---

# 68. IMPORTANT: DO NOT OVERFIT THE GOAL SCANNER

The goal scanner must not be trained to maximize historical "winning picks" without considering probability calibration.

A model that predicts:

```text
95%
```

for everything will look excellent if measured badly.

Use:

* Brier
* log loss
* calibration
* out-of-sample testing

as primary evaluation tools.

---

# 69. MODEL SELECTION CRITERIA

When comparing models, select the ensemble based primarily on:

1. Out-of-sample log loss
2. Brier score
3. calibration
4. sharpness
5. stability across competitions
6. stability across seasons
7. incremental information relative to market

Do not select solely on hit rate.

---

# 70. FINAL MODEL ARCHITECTURE

The desired production pipeline is:

```text
                 RAW DATA
                    |
        +-----------+-----------+
        |           |           |
      Results     xG/Stats     Odds
        |           |           |
        +-----------+-----------+
                    |
              Data Validation
                    |
             Feature Builder
                    |
       +------------+-------------+
       |            |             |
       v            v             v
 Dixon-Coles      Elo       Hierarchical
       |            |          Poisson
       +------------+-------------+
                    |
                    v
             ML Goal Model
                    |
                    v
             Ensemble Layer
                    |
                    v
          Probability Calibration
                    |
                    v
          Goal Probability Engine
                    |
       +------------+-------------+
       |            |             |
       v            v             v
   O1.5/O2.5     BTTS/xG       1X2/Score
       |            |             |
       +------------+-------------+
                    |
                    v
            Goal Opportunity
                 Scanner
                    |
                    v
             Prediction Snapshot
                    |
          +---------+---------+
          |                   |
          v                   v
       Frontend             Telegram
```

---

# 71. USER EXPERIENCE PRIORITY

The primary user flow should become:

```text
OPEN APP
   ↓
SEE TODAY'S TOP GOAL OPPORTUNITIES
   ↓
FILTER ALL LEAGUES
   ↓
SELECT MATCH
   ↓
SEE PROBABILITIES
   ↓
SEE WHY
   ↓
COMPARE MARKET
   ↓
SEE HISTORICAL MODEL PERFORMANCE
```

Do not force users through complicated filters before showing useful information.

---

# 72. HOME PAGE

Top section:

```text
TODAY'S GOAL INTELLIGENCE

342 Matches Analysed

87 Strong Goal Signals
31 Value Opportunities
12 Live Matches
```

Then:

```text
TOP GOAL OPPORTUNITIES
```

Then:

```text
ALL LEAGUES
```

Then:

```text
MODEL PERFORMANCE
```

---

# 73. DO NOT FABRICATE DATA

This is mandatory.

If:

* odds are unavailable
* lineup unavailable
* xG unavailable
* historical sample insufficient
* competition data incomplete

say so.

Never invent:

* odds
* xG
* injuries
* lineups
* weather
* historical results
* probabilities

---

# 74. DO NOT CLAIM "ALL LEAGUES" IF DATA IS INCOMPLETE

The application may scan:

> All supported competitions with valid current fixture data.

Use that wording rather than pretending that every football competition in the world is covered.

Create a coverage page:

```text
Competition Coverage

Premier League       ✓
La Liga               ✓
Serie A               ✓
Bundesliga            ✓
...
```

---

# 75. IMPORTANT PRODUCT DISTINCTION

Separate these concepts visually:

### Probability

What the model thinks is likely.

### Value

Model probability relative to available market price.

### Goal Potential

A ranking derived from validated goal-related features.

### Confidence

How reliable the underlying data/model agreement is.

These must not be represented as the same number.

---

# 76. DELIVERABLES

At the end of implementation provide:

1. Updated frontend.
2. Updated backend.
3. Updated prediction engine.
4. Goal Scanner.
5. Model ensemble.
6. Calibration system.
7. Prediction snapshots.
8. Model performance dashboard.
9. Model methodology page.
10. Value engine.
11. Odds provenance.
12. Prediction history.
13. Data quality monitoring.
14. API health endpoint.
15. Automated tests.
16. Database migrations where necessary.
17. Deployment configuration.
18. Updated environment variable documentation.
19. README.
20. Architecture documentation.

---

# 77. IMPLEMENTATION ORDER

Do not attempt everything simultaneously.

Implement in this order:

## Phase 1

Audit current application.

## Phase 2

Prediction snapshot/version infrastructure.

## Phase 3

Walk-forward evaluation framework.

## Phase 4

Improve Dixon-Coles + Elo.

## Phase 5

Hierarchical Poisson.

## Phase 6

ML goal model.

## Phase 7

Ensemble.

## Phase 8

Calibration.

## Phase 9

Goal Scanner.

## Phase 10

Value/odds engine.

## Phase 11

Model performance dashboard.

## Phase 12

Prediction explanation.

## Phase 13

Mobile UX.

## Phase 14

Infrastructure optimization.

## Phase 15

Automated testing.

---

# 78. ACCEPTANCE CRITERIA

Do not consider the project complete until:

### Prediction

* all probabilities are between 0 and 1
* probability distributions sum correctly
* predictions are reproducible
* historical predictions are immutable
* model version is stored
* no future data leakage exists

### Goal Scanner

* scans all currently supported competitions
* supports date filtering
* supports league filtering
* ranks by calibrated probability
* exposes xG
* exposes O1.5
* exposes O2.5
* exposes BTTS
* exposes model agreement
* handles missing data correctly

### Value

* uses timestamped odds
* calculates implied probability
* calculates EV
* clearly shows bookmaker/source
* marks stale prices

### Performance

* calibration chart works
* Brier score works
* log loss works
* historical results work
* probability buckets work

### UX

* mobile responsive
* desktop responsive
* fast loading
* clear loading states
* clear error states
* no misleading confidence language

### Infrastructure

* API health works
* scheduler failures are isolated
* ingestion failures are visible
* database integrity is protected
* logs are structured
* expensive model operations do not block API requests

---

# 79. FINAL PRODUCT PRINCIPLE

The most important principle for the entire application is:

> **Do not try to predict football with certainty. Build the best calibrated probability estimates possible, expose the uncertainty, compare independent models, record every prediction before kickoff, and prove performance with out-of-sample evidence.**

The system should be judged by the quality of its probabilities, not by how impressive individual winning picks look.

Start by inspecting the existing codebase and produce the implementation plan. Then implement the changes incrementally, testing each phase before moving to the next. Do not replace functioning architecture without first establishing a measurable benefit.
