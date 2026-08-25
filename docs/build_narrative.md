# What building this dataset taught

*Phase 2B — a reproducible simulation of an Indian marketplace's checkout, built to
support a case study on COD, RTO risk and contribution margin. 105,605 orders,
155,000 sessions, a known ground truth planted underneath.*

This page is about the build, not the output. The output is in
`reports/data_validation_report.md`; the full reasoning is in
`docs/decision_register.md`, 45 rulings long. What follows is the part worth
carrying to the next project.

The one-line version: **the dataset was the easy half.** The hard half was
noticing, repeatedly, that things which looked like verification were not
verification.

---

## 1. Four places the specification contradicted itself

The spec was written before any code ran. Four of its internal inconsistencies
only surfaced once the numbers were real. In each case the interesting part is
not the defect — it is *what the ruling had to turn on*, because there was no
purely technical answer available.

**A7 — three hard targets, one knob.** COD RTO (24%), prepaid RTO (4.1%) and
blended RTO (16.5%) were all HARD. But the only free parameter is the RTO
intercept, and moving it shifts all three together; the COD/prepaid *split* is
not tunable at all. Three constraints, one degree of freedom.

The tempting fix was a second knob — widen the gap by moving a latent slope. That
was refused: slopes are immutable, and tuning one to hit a rate would have made
the planted causal structure a *consequence* of the target rather than a cause of
it. Instead, only the blended rate stayed HARD; the other two were relabelled
**emergent, not calibrated**. And the vacated hardness went somewhere better: a
new HARD gate, CAL-11, on the *selection share* — the fraction of the naive
COD−RTO gap that is confounding rather than causation, required to land in
[0.25, 0.45].

*What the ruling turned on:* the difference between testing a **level** and
testing a **structure**. Levels are what one knob steers. Structure is what the
case study is actually about — the whole payoff is "the naive analysis overstates
the COD effect" — so structure is what deserved the HARD test. A dataset can hit
every rate target and still be useless for the argument it exists to support.

**A34 — is ₹1,000 the GMV or the order value?** Two spec sections labelled
`order_value` as "the ₹1,000 AOV quantity". Phase 1 said GMV ₹1,000, less an 8%
discount, gives net revenue ₹920. An 8% ambiguity, easy to wave through.

It was not waveable, because the two readings are not both consistent with the
rest of the model. If ₹1,000 is `order_value`, mean GMV becomes ₹1,087, and
24M orders × ₹1,087 = **₹2,609 Cr — not the ₹2,400 Cr headline the entire
opportunity model rests on.** Every locked unit-economics figure (+₹112, +₹107,
−₹309, −₹416, p\* = 25.7%) was computed from a ₹1,000 *GMV* order.

*What the ruling turned on:* document seniority. Phase 1's business framing
outranks Phase 2's implementation spec, so Phase 1 won and the mislabel was
recorded as a Phase 2A defect. Ambiguity in a definition is never local — it
propagates into every figure derived from it, and the way to resolve it is to ask
which document the rest of the model was built from.

**A37 — the spec asked for an AUC it could not produce.** The spec targeted a
risk-model ceiling of 0.74–0.79. Its own coefficients, at its own
`noise_sd = 0.85`, produced **0.8745**.

This was worse than a failed test. A separate guard, LK-03, flags any
safe-feature model scoring above 0.85 as evidence of leakage. If the theoretical
ceiling is already 0.87, that guard cannot distinguish "something leaked" from
"the process is genuinely predictable." **The tripwire had stopped tripping.**

Rather than pick a value that worked, the parameter was swept and read across:

| `noise_sd` | AUC | prepaid RTO | naive gap |
|---:|---:|---:|---:|
| 0.85 (spec) | 0.8745 ✗ | 2.61% ✗ | 22.5pp ✗ |
| 3.00 | 0.7833 ✓ | 5.46% ✓ | 18.6pp ✓ |

*What the ruling turned on:* **three independent expectations missing together,
and converging together.** One failing number is a judgement call. Three failing
in the same direction and landing simultaneously when one input moves is evidence
about *which* input is wrong. The value was then calibrated against its declared
purpose — solved to 3.3125, not chosen — with the RTO intercept re-solved inside
every iteration so the blended rate was held while the AUC moved.

**A38 — the same cost stated twice, two different ways.** The spec gave cost
parameters as formulas *and* as blended means, and they disagreed. Shrink: the
per-category rates weight to 9.72%; the text said "≈8.0%". NDR: the formula
`18 + 6 × (attempts − 1)` returns ₹30 on an RTO; the text said ₹18.

*What the ruling turned on:* **the two gaps had different causes, so they got
different answers.** The shrink discrepancy was arithmetic — the text simply
mis-added its own inputs, so the formula won and the text was restated. The NDR
discrepancy was semantic: Phase 1's registry said "₹18 **per RTO**" as a total,
and Phase 2 had reinterpreted it as a *base*, producing a formula that can never
return ₹18 for an order that by definition exhausted all three delivery attempts.
There the parameter won.

Splitting was the point. Applying one rule to both — "formulas always win", or
"targets always win" — would have been tidier and wrong in one of the two cases.

---

## 2. The pattern that cost the most to find

Three times, in three unrelated subsystems, a check was found that could not
fail. Each looked completely correct.

| The check | Where its reference came from | What it could not see |
|---|---|---|
| **CAL-09** — "no coefficient differs from `params.yaml`" | The comparison ledger was rebuilt *from `params.yaml`* | Everything. It compared the config file against a copy of itself, and would have passed regardless of what the generator actually multiplied |
| **DQ-01** — reproducibility | The manifest was compared against **the run that wrote it** | Everything. That proves a hash function is deterministic, nothing more |
| **Staleness gate** on the database results file | Keyed on the **dataset hash** alone | A schema change. The last ruling deliberately changed the schema while leaving the data byte-identical — so a stale file would have reported "102 constraints verified" as current evidence about a 104-constraint schema |

The generalisation:

> **A check whose reference is derived from the thing it checks is not a check.**
> It is a restatement dressed as a verification.

What makes this expensive is that it fails *silently by construction*. A
tautological check never goes red, so nothing ever draws attention to it. It
produces exactly the output a working check produces. The only way to find one is
to ask, deliberately and about each check in turn: **what independent thing is
this being compared against?** If the honest answer is "itself, one step
removed", there is nothing there.

Every fix had the same shape — not a stronger assertion, but a **second,
genuinely independent observation**. A ledger recording what the code actually
consumed at runtime. A second generation run from the same seed. A hash of the
schema alongside the hash of the data.

The same shape appears in two idioms that look like diligence and are not.
`ALTER TABLE … VALIDATE CONSTRAINT` asks the catalogue whether a constraint is
marked valid — and a normally-created constraint is marked valid on creation, so
the answer comes from the act of creating it, not from the rows. Reading
`pg_catalog` to confirm a permissions boundary reports what the schema
*intended*, not what a real login is *refused*. Both were replaced with something
that touches reality: an anti-join per foreign key, a predicate scan per check,
and an actual denied `SELECT` as the restricted role.

And the last check was verified by **breaking it on purpose** — corrupting the
schema hash and confirming the three affected tests degraded to SKIP rather than
reporting a stale pass. Trusting a fix is the same mistake one layer up.

---

## 3. What happened the first time the schema met real rows

The database schema was written early and carefully: 102 CHECK predicates, every
foreign key, NOT NULL on everything required. It was the most precise statement
of intent in the project.

It had never been run. For all of Phase 2B the pipeline wrote Parquet, and Parquet
has no NOT NULL, no CHECK, no foreign keys. **A column that is 100% NULL is a
perfectly well-formed Parquet column.** The first load into PostgreSQL found six
defects in one sitting — after 146 unit tests and 42 data-validation tests had
passed.

Neither test layer was capable of seeing them, and that is the point. Unit tests
check what the *code* does; a column the schema declares and the code never
mentions has no code to test. Data tests check relationships someone *thought
of*; all six defects lived in relationships declared once and never revisited.

> **Constraints are only constraints once executed.** A rule no engine has read is
> prose, however precisely it is written.

Five of the six announced themselves loudly — a NOT NULL violation on the first
row, a broken cost identity, an empty column. **The sixth did not, and it was the
expensive one.**

`pit_days_since_last_order` was never populated. The day loop computed the
underlying value and nothing consumed it. It would have loaded cleanly, satisfied
every foreign key, and passed all 42 data-validation tests — because it is on the
**whitelist of features the risk model is permitted to train on**. A model would
have trained on a column of nulls, scored slightly worse, and produced no error
anywhere. The most likely outcome is that nobody ever finds out, because a
slightly worse AUC is indistinguishable from a slightly harder problem.

Two things follow. **Loud failures are cheap; silent ones are not** — the severity
ordering of these six is inverted relative to their noisiness. And **constraints
and diffs catch different things**: a constraint catches a violation of something
you asserted, and can never catch the *absence* of something you forgot to
assert. That one was found by diffing the DataFrame's columns against the
database's — two lists that were supposed to agree, written months apart, that
did not. No predicate was violated. Something was merely missing.

Executing the schema also found a defect *in the schema*: a NOT NULL on a
customer-level average, where 6% of customers have no sessions and therefore no
denominator. The column is now nullable. A statistic with no data behind it stays
NULL — it is never imputed, because a fabricated value inside the ground-truth
table is the one place nothing downstream could catch it.

---

## 4. Three predictions that were wrong, and left wrong

Hypotheses were registered with numeric priors *before* generation. Four could be
measured directly against the built dataset; **three of them missed.** None was
adjusted afterwards, because a documented wrong prior is stronger evidence of
real analytical work than a full set of correct ones.

| | Predicted | Measured | |
|---|---|---:|---|
| **H2** New customers' COD lift | 12–18pp | **22.5pp** | above |
| **H3** Repeat-RTO customers' risk multiple | 2.0–2.5× | **1.69×** | below |
| **H11** Share of COD caused by payment friction | 8–15% | **5.9%** | below |

**H2** overshot because the prior priced one coefficient in isolation. Being new
carries a direct +0.70 on the COD logit — but a new customer *also* escapes two
negative terms that only accumulate with history (delivered orders, successful
prepaid payments). The prior counted the term it could see and missed the two
absences. A useful reminder that in an additive model, what a row *doesn't* have
is as much a driver as what it does.

**H3** undershot as a direct consequence of ruling A37. Raising post-dispatch
noise from 0.85 to 3.3125 was necessary to make the AUC ceiling honest, and it
dilutes *every* pre-checkout signal — including the customer's own prior-RTO
rate, which is exactly what H3 measures. The prior was formed before that ruling
existed. **This is a real cost of A37, not a coincidence**, and it is recorded as
one rather than explained away.

**H11** undershot because the prior and the parameters came from different
places. The payment-failure rates were set from plausible external gateway
figures; the 8–15% prior was an independent guess about the outcome. They were
never reconciled, and the spec had itself predicted the mismatch.

Three different failure modes, worth separating: **an incomplete mental model**
(H2), **a downstream consequence of a later decision** (H3), and **two
assumptions never checked against each other** (H11).

---

## 5. Where it landed

The planted effect of COD on RTO is **+9.99pp**. The naive
COD-versus-prepaid comparison — the one an analyst would run first — reports
**17.73pp**. The gap is confounding: a hidden low-commitment trait drives both
the choice to pay cash and the tendency not to take delivery.

**The naive answer is 1.77× the truth**, and 44% of the apparent effect is
selection. That number is the reason the dataset exists, and it is why one test
is designed to *fail* to fully recover the truth: an adjusted estimate that lands
exactly on the planted value would mean the unobservable had leaked.

Final state: 65 validation tests, 59 pass, 0 hard failures, 0 soft failures, 6
deferred to a later phase because they need fitted models. 170 unit tests. Every
figure downstream reads a machine-written truth file rather than the spec's
prose, because on three separate occasions the prose turned out to describe a
dataset that no longer existed.

---

## The short version

Building a dataset with a known answer is mostly an exercise in **not fooling
yourself**. The spec will contradict itself, and the contradictions will be
invisible until the numbers are real. Checks will validate against their own
output and look green forever. Schemas will describe things nothing produces. The
quietest defect will be the most expensive one.

The habits that actually caught things:

- Ask of every check: **what independent thing is this compared against?**
- **Execute the constraint.** An unexecuted rule is prose.
- **Diff two descriptions that should agree** — a constraint cannot catch an absence.
- When several expectations miss together and land together, **the shared input is the defect.**
- **Test the fix by breaking it**, not by trusting it.
- Leave the wrong predictions in.
