# PHASE 1 — PROJECT BLUEPRINT

## E-commerce Checkout Optimization: Reducing RTO While Protecting Conversion and Contribution Margin

**Document type:** Business + analytical foundation (pre-build)
**Phase:** 1 of 6
**Status:** Framework locked → gate for Phase 2 (dataset design & generation)
**Owner role simulated:** Senior PM (Checkout & Payments) with Product Analytics + Experimentation leads

---

## 0. HOW TO READ THIS DOCUMENT

This is a **portfolio case study built on simulated data**. Nothing here is a claim about any real company. Every number is tagged so that in an interview you can say exactly what kind of number it is. If you cannot say which of these four buckets a number falls into, do not put it on a slide.

| Tag | Meaning | Example | How you defend it in interview |
|---|---|---|---|
| **[F] Fact** | Publicly observable / structurally true / arithmetic identity | "RTO means the parcel travels back to the seller, so the network pays two legs of freight and earns zero revenue" | Logic or citation |
| **[A] Assumption** | A parameter *I chose*, calibrated to be plausible, that drives results | "COD share = 62% of orders" | "It's a stated input. Here's the sensitivity band and here's what breaks if it's wrong." |
| **[S] Simulated** | A value produced by the data generator in Phase 2 | An individual order's `rto_flag` | "Generated from a specified causal structure, not observed" |
| **[D] Derived** | Computed from [S] data using a stated formula | Blended RTO rate = 16.5% | "Reproducible from the dataset with this SQL" |

**The one sentence that protects this whole project:**
> "This is a simulation with a known ground truth. The point isn't that the numbers are real — it's that the *decision framework, the economics, and the experiment design* are the ones I'd use on real data, and because I control the DGP I can prove my analysis recovers the truth I planted."

**Critical honesty rule for this project:** we build the model *first* and let it produce whatever number it produces. Section 7 shows exactly what scale and cost assumptions are required for a ₹165 Cr headline, and states plainly that the number is an **exposure**, not a **capture**.

---

## 1. BUSINESS CONTEXT

### 1.1 The simulated marketplace: "BharatKart"

| Attribute | Value | Tag |
|---|---|---|
| Model | Managed marketplace (platform owns unit economics on ~all orders; take-rate variant shown in §6.7) | [A] |
| Annual orders | 24,000,000 (2M/month) | [A] |
| Annual GMV | ~₹2,400 Cr | [D] |
| Average order value | ₹1,000 | [A] |
| Categories | Fashion, Beauty, Home & Kitchen, Mobile Accessories, Electronics, Grocery/FMCG | [A] |
| Geography | Metro / Tier-1 / Tier-2 / Tier-3+ | [A] |
| Payment mix | COD 62% / Prepaid 38% | [A] |
| Blended RTO rate | 16.5% (COD 24.0%, Prepaid 4.1%) | [A] → [D] in Phase 2 |
| Analytical dataset | 100,000 orders = **5% random sample of one month** | [A] |

**Why these particular numbers:** they sit inside the publicly discussed ranges for Indian horizontal marketplaces — COD share 55–70%, COD RTO 18–30%, prepaid RTO 3–6%, RTO cost 4–8% of GMV. I am not claiming they are any specific company's numbers. They are *calibration targets* so the simulation behaves like the real system rather than like noise.

**Why the sample is 5% of a month, not "one month":**
This is the single most important scale decision in the project, and it is the honest answer to "how do you get to ₹165 Cr from 100K orders?" A 100K-order dataset that represents one month of a 100K-orders/month marketplace annualizes to 1.2M orders, which **cannot** produce a ₹165 Cr RTO number under any credible cost assumption (it produces roughly ₹8 Cr). So we declare up front that the 100K rows are a **stratified sample of a larger population**, and we carry an explicit annualization factor of **×240** (×20 sample→month, ×12 month→year). See §7.

### 1.2 What RTO is [F]

**Return to Origin (RTO)** = an order that is shipped but never successfully delivered, and is therefore transported back to the origin (seller/warehouse). It is *not* a return. The distinction matters enormously:

| | RTO | Customer return |
|---|---|---|
| Did the customer receive the goods? | No | Yes |
| Did the customer ever pay? | Usually no (COD) | Yes, then refunded |
| Freight legs paid | 2 (forward + reverse) | 2 |
| Revenue recognised | ₹0 | Reversed |
| Product condition on receipt | Usually resaleable | Often not |
| Root cause | Intent, trust, address, availability | Fit, quality, expectation gap |
| Where the fix lives | **Checkout / pre-purchase** | Catalog, sizing, QC |

RTO is a **pre-delivery failure**, which is exactly why it is a *checkout* problem and not a *logistics* problem. Logistics can shave a few points with better address resolution and re-attempt logic; the structural driver is what happened at the moment of commitment.

Common RTO reasons [A, to be simulated in Phase 2]: customer unreachable, customer refused delivery, address incorrect/incomplete, customer says "I didn't order this", customer changed mind, no cash available at delivery, delivery attempted outside serviceability window.

### 1.3 Why RTO is expensive [F]

An RTO order is the worst possible order: **you pay full variable cost and earn zero revenue.**

Costs incurred on an RTO that are *not* recovered:
1. **Forward freight** — already paid to move the parcel out
2. **Reverse freight** — paid again to move it back (often at similar or higher rate, since reverse legs are lower-density)
3. **Pick, pack, packaging material** — consumed and destroyed
4. **COD collection attempt cost** — the courier attempted collection and failed
5. **Warehouse re-inward + QC + restocking** — a full inbound touch
6. **Shrinkage / damage / obsolescence** — a meaningful share of returned units cannot be sold at full price (fashion sizes, dated packaging, seals broken)
7. **Customer support + ops handling** — calls, NDR (non-delivery report) resolution, re-attempts
8. **Working capital carried** — inventory blocked in transit for ~20–35 days
9. **Foregone contribution margin** — the margin the order would have earned had it landed
10. **Second-order effects** — capacity in the network consumed by parcels that were never going to convert; marketing spend that acquired an order that produced nothing

Items 1–8 are **cash costs**. Item 9 is an **opportunity cost**. Item 10 is real but hard to attribute, and we will deliberately exclude it to keep the model conservative and defensible.

> **Interview point:** most candidates quote RTO cost as "two-way shipping." That is roughly 40% of the true cost. The ability to decompose it into nine lines and then say "I excluded the tenth because I couldn't attribute it cleanly" is what separates a PM from a dashboard operator.

### 1.4 Why COD can increase RTO [F for mechanism, A for magnitude]

COD changes the **commitment structure** of the transaction. Under prepaid, the customer has already transferred money; the psychological and financial cost of abandoning the parcel is high. Under COD, the customer has posted **zero collateral**. The option to refuse at the door is free.

Concretely, COD enables three failure modes that prepaid structurally cannot have:
- **Free optionality** — "order it, decide later" behaviour, including ordering multiple variants intending to keep one
- **Liquidity failure at the door** — the customer intends to buy but has no cash on the delivery day
- **Impulse decay** — the gap between order and delivery (3–7 days) is long enough for intent to evaporate, and nothing anchors the customer to the decision

**But — and this is the central analytical trap of the project — this is a mechanism argument, not a causal estimate.** COD is *selected into* by customers who are also, independently, higher-RTO-risk (new, Tier-3, low-trust, low-tenure). Any raw COD-vs-prepaid RTO gap is a **confounded** comparison. Phase 5 must decompose the observed gap into a selection component and a treatment component. See H1 in §4 and Q1 in §13.

### 1.5 Why customers still prefer COD [A — hypotheses, not conclusions]

Do **not** write "customers use COD because they don't trust us" on a slide until §7 of the main study tests it. The candidate explanations are:

1. **Payment-instrument distrust** — fear of money debited without order confirmation, card fraud, failed-transaction refund delays
2. **Seller/product distrust** — "will I get the real product, in the right size, undamaged?"
3. **Delivery uncertainty** — "will this even arrive by the date shown?"
4. **Habit / default** — COD is what they have always used; it is often the pre-selected or most familiar option
5. **Inspect-before-pay** — a legitimate consumer-protection preference, especially for apparel and unbranded goods
6. **Financial access & liquidity** — no card, thin bank rails, cash-based household budgeting, salary-cycle timing
7. **Prepaid checkout friction** — OTP failures, bank downtime, redirect flows, UPI collect timeouts. The customer may *prefer* prepaid and be *pushed* to COD by a broken funnel

Note that (6) and (7) are **not trust problems at all** — one is an access problem, one is a reliability problem. If the true driver is (7), a trust-messaging intervention will do nothing and a payment-reliability fix will do everything. Committing to "trust" prematurely is the most likely way this project produces a wrong recommendation.

> **Interview point:** the resume bullet says *"identifying trust as a COD driver"* — singular, indefinite article, "a driver." Defend it that way. Say: "trust proxies explained a material share of COD selection after controlling for tenure and geography, but liquidity and payment reliability were also significant, and that's why the intervention set isn't purely messaging."

### 1.6 Why "just force everyone to prepaid" is a bad product strategy [F/A]

This is the obvious answer and it is wrong. Five reasons:

1. **Demand destruction dominates.** [A] If COD is 62% of orders and removing it converts only ~25–35% of those customers to prepaid, you lose ~40% of total orders. No RTO saving survives that.
2. **You lose the highest-growth cohort.** COD skews toward Tier-2/3 and new customers — the incremental TAM. Killing COD is killing the top of the funnel to fix a leak at the bottom.
3. **Adverse selection.** The COD customers who *would* switch to prepaid are disproportionately your good customers. The ones who refuse are the ones you wanted to keep. You may end up with a *worse* residual mix.
4. **Competitive leakage.** In a market where every competitor offers COD, removing it is a one-click migration to a competitor — and the customer is unlikely to come back.
5. **It is a policy, not a product.** A blanket rule can't distinguish a 40-order loyal customer paying cash out of preference from a 0-order account with three prior RTOs. Treating them identically destroys value in both directions.

**The correct framing:** COD is not a defect to eliminate. It is a **product feature that carries a risk premium**, and the PM's job is to *price and allocate that premium intelligently* — the same way an insurer doesn't refuse to insure drivers, it segments them.

### 1.7 Why this is a multi-objective optimisation problem

Six objectives, and every lever moves at least three of them, usually in opposite directions:

| Lever | Conversion | COD share | RTO | GMV | Contribution margin | CX |
|---|---|---|---|---|---|---|
| COD fee | ↓ | ↓↓ | ↓↓ | ↓ | ? (net of conversion loss) | ↓ |
| Prepaid discount/cashback | ↑ | ↓ | ↓ | ↑ | ? (discount cost vs RTO saving) | ↑ |
| Trust messaging | ↔/↑ | ↓ (small) | ↓ (small) | ↔ | ↑ if free | ↑ |
| COD blocking (high-risk) | ↓↓ | ↓↓↓ | ↓↓↓ | ↓↓ | ? | ↓↓ |
| Partial prepayment | ↓ (small) | ↓ | ↓↓ | ↔ | ↑ | ↔ |
| Prepaid UX/reliability fix | ↑ | ↓ | ↓ | ↑ | ↑ | ↑ |

Notice the last row: **the only unambiguously positive lever is fixing prepaid reliability.** That is a genuine finding of the framing exercise, and it should shape the intervention prioritisation before any data exists.

**The trade-off geometry, stated precisely:**
- Conversion and RTO are **positively coupled through COD**: anything that makes COD less attractive reduces both.
- GMV and contribution margin **diverge**: you can grow GMV while destroying margin (discounting) or shrink GMV while growing margin (COD fees).
- CX is a **constraint, not an objective** — it belongs in guardrails, because a margin-optimal checkout that customers hate produces a delayed, invisible retention cost the experiment window can't see.

This is why **no single metric can govern this project.** See §5.

---

## 2. THE CORE PROBLEM

### 2.1 Challenging the given framing

You proposed:
> *"How might we reduce avoidable RTO while maintaining checkout conversion and improving contribution margin?"*

This is a good framing and I would still **change two things**.

**Challenge 1 — "reduce RTO" makes a cost line the objective.** RTO is an *output*, not a goal. There is a trivial way to drive RTO to near zero (prepaid-only) that destroys the business. Any framing that names a cost line as the target invites the optimiser to over-serve it. The objective should be the *economic outcome*; RTO should be a *diagnostic*.

**Challenge 2 — "maintaining checkout conversion" measures the wrong conversion.** Conversion is normally `orders / checkout sessions`. But an order that RTOs is worse than no order at all — it costs ~₹309 in cash. So the existing conversion metric **rewards a negative-value event**. We need:

> **Net Conversion = successfully delivered orders / checkout sessions started**

This single redefinition resolves most of the tension in the project. Under Net Conversion, a COD fee that removes low-intent orders can *raise* conversion. Under the old definition it always looks like a loss.

### 2.2 Final problem statement (locked)

> **BharatKart's checkout converts demand into orders efficiently but converts orders into *delivered, paid-for* orders inefficiently. 62% of orders are COD [A]; COD orders fail at delivery ~5.9× the prepaid rate [A], destroying an estimated ₹165 Cr of annual contribution value [D, §7]. The checkout currently treats every customer identically at the payment step — a 40-order loyal customer and a first-time account with a prior RTO see the same payment options, the same fees, and the same trust signals.**
>
> **How might we make the payment step risk-aware — allocating incentives, fees, trust signals and payment options according to each order's predicted delivery-failure risk — so that we increase contribution margin per checkout session, without reducing net conversion or degrading the experience for low-risk customers?**

**Why this framing is better:**
- It names the *product defect* ("treats every customer identically"), not the *symptom* (RTO)
- The objective is economic, the constraint is customer-facing
- "Without degrading the experience for low-risk customers" pre-commits to fairness — which is the question every good interviewer asks (Q7 in §13)
- It is falsifiable: risk-based allocation may fail to beat a flat policy (H10)

### 2.3 The four objectives

| Objective | Statement | Why this one | Success looks like |
|---|---|---|---|
| **Business** | Increase contribution margin per checkout session started, at flat-or-better net conversion | CM/session is the only metric that internalises *both* the funnel and the fulfilment outcome; you cannot game it by killing demand or by buying orders with discounts | +₹X/session, net conversion within guardrail |
| **Customer** | Let customers pay in the way they're comfortable with, and give them enough information at checkout to be comfortable paying earlier | The customer's problem isn't "I want COD," it's "I'm not sure enough about this purchase to commit money to it yet." Solve the uncertainty, and payment method follows | COD users voluntarily choosing prepaid; no increase in complaint/abandon rate |
| **Product** | Ship a real-time checkout risk engine + intervention decision layer, with a differentiated experience per risk tier | The defect is uniformity. The product is *differentiation infrastructure*, not a specific fee or discount | Risk score served <100ms at checkout; interventions configurable without deploy |
| **Analytical** | Estimate delivery-failure risk pre-purchase using only pre-checkout features, and estimate the causal effect of each intervention on CM/session | Two distinct jobs — **prediction** (who is risky) and **causal inference** (does the intervention work). Conflating them is the classic failure mode | Risk model AUC ≥ 0.70 [A target]; experiment with clean causal read on CM |

### 2.4 North-star metric

> **North Star = Contribution Margin per Checkout Session Started (CM/CSS)**

$$\text{CM/CSS} = \frac{\sum_{\text{orders}} \text{Contribution Margin (incl. negative CM on RTO orders)}}{\text{Checkout sessions started}}$$

**Why this and not the alternatives:**

| Candidate | Why rejected |
|---|---|
| RTO rate | Minimised by destroying demand. Also has a 15–30 day feedback lag — unusable for real-time decisions or short experiments |
| Checkout conversion | Rewards RTO orders as successes. Optimised by removing all friction and all fees, which is exactly the current failure |
| Contribution margin (absolute) | Not normalised — grows with traffic, so it can't be compared across experiment arms of different sizes, and it can't diagnose |
| CM per **order** | **The most dangerous near-miss.** Maximised by *rejecting orders*. Block all COD → CM/order rises beautifully → business collapses. The denominator must be demand, not accepted demand |
| Delivered order value / GMV | Ignores cost structure entirely. A ₹2,000 delivered order at 3% margin is worse than a ₹600 order at 15% |
| CM/CSS | **Numerator = money actually kept. Denominator = demand actually exposed.** Cannot be gamed from either end |

**The lag problem and how we solve it.** CM/CSS is only *fully* observable ~T+30 days (once delivery outcomes settle). That is too slow for a decision engine and awkward for an experiment. So we run **two versions**:

- **`CM_expected/CSS`** — uses the model's predicted RTO probability to compute expected margin. Available instantly. Used for real-time intervention decisions and for the *early read* on an experiment.
- **`CM_realized/CSS`** — uses actual delivery outcomes. Available T+30. The **only** metric on which we make the final launch call.

> **Interview point:** "My north star has a 30-day lag, so I ship a leading proxy for decisioning and I never let the proxy make the launch decision. The proxy's job is to tell me whether to *keep the experiment running*; the realised metric's job is to tell me whether to *ship*."

---

## 3. PROBLEM TREE

Root: **High avoidable RTO / low contribution margin per checkout session**

Every node is tagged. **H** = hypothesis (unproven). **E** = evidence required. **D** = data required. Nothing below is asserted as true.

### Branch 1 — CUSTOMER

| Node | Status | Evidence required | Data required |
|---|---|---|---|
| Low trust in platform/payment rails | H | COD share elevated among users with no prior prepaid success, *after* controlling for tenure & geography | `historical_prepaid_success`, `payment_failure_history`, tenure, geo |
| New customer with no track record | H | COD rate and RTO rate by `historical_orders` bucket; check whether effect persists after order-2 | `customer_tenure_days`, `historical_orders` |
| Previous poor experience (late/failed delivery) | H | RTO rate conditioned on prior order's `delivery_delay_days` and prior RTO | prior-order delivery SLA breach flag |
| Habitual COD preference | H | `historical_cod_share` predicts current COD selection *even when* order/product features are held constant | `historical_cod_usage`, order-level controls |
| High-value purchase anxiety | H | COD share rising monotonically with `order_value` within the same category & customer tier | `order_value`, `product_category` |
| Liquidity constraint (no cash at door) | H | RTO reason = "no cash available"; concentration near month-end / salary cycle | `return_reason`, `order_date` day-of-month |
| **Low purchase intent / free optionality** | H | Multiple simultaneous COD orders of same product in different variants; short session duration; no PDP dwell | session events, order clustering |

### Branch 2 — PRODUCT

| Node | Status | Evidence required | Data required |
|---|---|---|---|
| Low product rating drives COD | H | COD share by `product_rating` decile, controlling for category & price | `product_rating`, `review_count` |
| Thin review count → uncertainty | H | COD share by `review_count` bucket, holding rating constant | `review_count` |
| Category-inherent uncertainty (fit/size) | H | Fashion/footwear show higher COD *and* higher RTO than Electronics at same AOV | `product_category` |
| Price-quality mismatch signals fraud | H | COD share spikes where `discount_pct` is extreme | `discount`, `product_price` |

### Branch 3 — SELLER

| Node | Status | Evidence required | Data required |
|---|---|---|---|
| Low seller rating drives COD & RTO | H | COD share and RTO rate by `seller_rating` band, controlling for category/price | `seller_rating` |
| Poor seller fulfilment history (dispatch delays) | H | RTO rate by seller's historical SLA breach rate | seller SLA history |
| Seller-side cancellations mislabeled as RTO | E | Data-quality check: separate `order_cancelled` from `rto_flag` | `order_cancelled`, cancel actor |

### Branch 4 — LOGISTICS

| Node | Status | Evidence required | Data required |
|---|---|---|---|
| Long delivery promise → intent decay → RTO | H | RTO rate by `estimated_delivery_days`, controlling for pincode tier | `estimated_delivery_days` |
| Promise-vs-actual gap is the real driver | H | RTO rate by `(actual − estimated)` delay, not by promise alone | `actual_delivery_days` |
| Multiple attempts → eventual RTO | E | This is a **consequence**, not a cause — must not be used as a model feature (leakage) | `delivery_attempts` |
| Hard-to-serve geographies | H | RTO by pincode tier / serviceability class | `delivery_location`, tier |

### Branch 5 — CHECKOUT

| Node | Status | Evidence required | Data required |
|---|---|---|---|
| **Prepaid payment friction pushes customers to COD** | H (high prior) | Payment-page reached but payment failed → next attempt is COD, within same session | `payment_page_reached`, `payment_success`, method switch events |
| Absent trust signals at payment step | H | A/B only. Observational data cannot test this — nothing varies | requires experiment |
| COD is the visual/interaction default | H | Position/pre-selection audit + eye-path; test by reordering | requires experiment |
| Unexpected fees revealed late | H | Abandonment spike between address and payment page where shipping fee first appears | funnel step events, `shipping_fee` |
| Return policy not visible pre-purchase | H | A/B only | requires experiment |

### Branch 6 — BEHAVIOURAL / ECONOMIC

| Node | Status | Evidence required | Data required |
|---|---|---|---|
| Zero-collateral commitment (the core COD mechanism) | H (strong prior, hard to prove observationally) | Would require exogenous variation in prepayment requirement → **this is what the partial-payment experiment tests** | experiment |
| Prior RTO predicts future RTO | H | RTO rate by `historical_rto_count`; check it isn't purely address-driven | `historical_rto_rate` |
| Discount-seeking / low commitment | H | RTO rate by `discount_pct` decile | `discount` |

### The tree's most important structural insight

Three branches (Customer-trust, Checkout-friction, Behavioural-collateral) **cannot be resolved with observational data at all.** They require experiments. This is not a weakness of the project — it is the *reason the project needs an experimentation framework*, and it is the honest bridge from "analysis" to "product."

> **Interview point:** "My problem tree has 22 nodes. Observational analysis can rank about 14 of them. The other 8 — the ones about trust, friction and commitment — have no variation in the data to exploit, so I designed the experiment specifically to create that variation. That's how I decided what to test first."

---

## 4. KEY HYPOTHESES

Twelve testable hypotheses. Each is stated so it can be **rejected**.

### H1 — COD orders have materially higher RTO than prepaid
| | |
|---|---|
| **Why it might be true** | Zero collateral at commitment; free option to refuse; liquidity failure at door |
| **Why it might be false / confounded** | COD is *chosen* by customers who are independently riskier (new, Tier-3, low tenure). The raw gap could be almost entirely selection |
| **Data needed** | `payment_method`, `rto_flag`, plus full confounder set: tenure, `historical_orders`, geo tier, `order_value`, category, seller rating |
| **Metric** | RTO rate by method; **adjusted** odds ratio from logistic regression with confounders; propensity-matched difference |
| **Analysis** | (1) Raw crosstab → (2) stratified rates within tenure×geo cells → (3) logistic regression, report raw vs adjusted OR → (4) PSM sensitivity |
| **Expected implication** | If adjusted gap remains large → payment method itself is a lever. If it collapses → **the lever is customer risk, not payment method**, and COD fees are misdirected |
| **Limitation** | No instrument for payment choice in synthetic data → cannot claim causality from observation alone. Honest ceiling: "strong association robust to observed confounders" |

### H2 — New customers have higher COD adoption
| | |
|---|---|
| **Why true** | No prior successful transaction to anchor trust; no saved payment instrument |
| **Why false** | Could be a geography artifact — new customers may simply skew Tier-3, where COD is the norm regardless of tenure |
| **Data** | `historical_orders`, `customer_tenure_days`, geo tier |
| **Metric** | COD selection rate by orders-completed bucket (0, 1–2, 3–5, 6–15, 16+) |
| **Analysis** | Cross-tab; then two-way with geo tier; check whether the tenure gradient survives within each tier |
| **Implication** | If yes → onboarding/first-purchase is the highest-leverage intervention point |
| **Limitation** | Tenure and prepaid-instrument availability are collinear; cannot fully separate |

### H3 — Customers with previous RTOs have higher future RTO probability
| | |
|---|---|
| **Why true** | Persistent traits: bad address, habitual optionality, chronic liquidity |
| **Why false** | Regression to the mean; a single RTO may have been a one-off courier failure |
| **Data** | `historical_rto_rate`, `historical_orders` |
| **Metric** | Forward RTO rate by prior-RTO count; lift vs base rate |
| **Analysis** | Time-ordered split (features from t-1, outcome at t) to avoid leakage |
| **Implication** | Foundation of the risk score. Also raises the **fairness question**: how many prior RTOs justify restricting COD? |
| **Limitation** | Small-n instability for customers with 1–2 orders; needs shrinkage/smoothing toward the population mean |

### H4 — Higher order values increase COD preference
| | |
|---|---|
| **Why true** | More money at risk → stronger desire to inspect before paying |
| **Why false** | Could invert at the top — high-AOV electronics buyers may be affluent, prepaid-native, card-holding |
| **Data** | `order_value`, `product_category`, customer tier |
| **Metric** | COD share by order-value decile, faceted by category |
| **Analysis** | Decile chart; test for non-monotonicity — expect an inverted-U, not a straight line |
| **Implication** | If non-monotonic, a linear "value-based COD fee" is wrong; needs bands |
| **Limitation** | Category and value heavily confounded |

### H5 — Low seller/product ratings increase COD usage
| | |
|---|---|
| **Why true** | Rating is the customer's primary quality proxy at the moment of decision |
| **Why false** | Ratings may be too compressed (most 4.0–4.5) to carry signal; customers may not look at them |
| **Data** | `seller_rating`, `product_rating`, `review_count` |
| **Metric** | COD share by rating band, controlling for category & price |
| **Analysis** | Regression of COD selection on ratings + controls; check effect size, not just significance |
| **Implication** | **This is the key test for the "trust" claim in the resume bullet.** If ratings predict COD after controls, trust is empirically implicated |
| **Limitation** | Ratings proxy *product* trust, not *platform* trust — they cannot distinguish H1-type from H2-type distrust |

### H6 — Longer delivery promises increase COD usage and RTO
| | |
|---|---|
| **Why true** | Longer wait = more intent decay; also signals lower fulfilment reliability |
| **Why false** | The driver may be the *promise-vs-actual gap* (broken promise), not promise length |
| **Data** | `estimated_delivery_days`, `actual_delivery_days` |
| **Metric** | COD share and RTO rate by promise-days; and by delay = actual − estimated |
| **Analysis** | Compare explanatory power of promise vs delay in the same model |
| **Implication** | If delay dominates → the fix is **logistics reliability**, and no checkout intervention will move it. Important negative result |
| **Limitation** | Promise length is endogenous to pincode serviceability |

### H7 — Trust signals at checkout shift some customers from COD to prepaid
| | |
|---|---|
| **Why true** | Reduces perceived risk exactly at the commitment moment |
| **Why false** | Customers may not read them; COD preference may be structural (no card) and immune to messaging |
| **Data** | **Not testable observationally — no variation exists.** Requires experiment |
| **Metric** | Prepaid selection rate (treatment vs control) |
| **Analysis** | A/B test, primary = CM/CSS, secondary = prepaid share |
| **Implication** | If it works, it's the cheapest possible intervention — near-zero marginal cost, no conversion downside |
| **Limitation** | Novelty effects; ceiling effects for customers without payment instruments |

### H8 — A prepaid incentive reduces COD but may hurt contribution margin
| | |
|---|---|
| **Why true** | Direct economic nudge; well-established behaviour |
| **Why false** | Most of the discount is paid to customers who **would have paid prepaid anyway** — pure margin leakage. This is the classic incrementality failure |
| **Data** | Experimental; plus counterfactual prepaid propensity per customer |
| **Metric** | Incremental prepaid conversions per ₹ of incentive; ΔCM/CSS |
| **Analysis** | Break-even: `incentive_cost < ΔP(RTO) × RTO_cost_per_order`. Must be evaluated **per risk tier** |
| **Implication** | **Do not offer prepaid incentives to low-risk customers** — they are already prepaid-inclined; the discount is pure loss. This is the single strongest argument for risk-based targeting |
| **Limitation** | Discount habituation and expectation-anchoring don't appear inside a 3-week test window |

### H9 — A COD fee reduces low-intent COD orders but may reduce conversion
| | |
|---|---|
| **Why true** | Imposes a small cost on the free option; screens out low-intent orders |
| **Why false** | Fee-aversion is often far stronger than fee magnitude (₹29 can kill more orders than a ₹29 price increase would); may drive the customer to a competitor entirely |
| **Data** | Experimental |
| **Metric** | Net conversion, COD share, RTO rate, ΔCM/CSS |
| **Analysis** | Break-even: fee is justified when `CM_lost_from_abandoned_orders < RTO_cost_avoided`. Because abandoned COD orders were disproportionately going to RTO, the loss is smaller than it looks |
| **Implication** | Only economically justified above a risk threshold — see §10 decision table |
| **Limitation** | Brand/perception damage is real and unmeasurable in-experiment; needs a qualitative guardrail |

### H10 — Risk-based intervention outperforms one-size-fits-all
| | |
|---|---|
| **Why true** | Interventions have heterogeneous treatment effects; targeting concentrates cost where value is highest |
| **Why false** | If the risk model is weak (AUC <0.65) or effects are homogeneous, targeting adds complexity for no gain. **Also: targeting error can be worse than no targeting** — a false-positive high-risk flag on a great customer is expensive |
| **Data** | Experimental with risk-tier stratification |
| **Metric** | ΔCM/CSS of targeted arm vs flat arm vs control |
| **Analysis** | Three-arm test; CATE by risk decile; uplift modelling |
| **Implication** | **This is the thesis of the whole project.** If H10 fails, the honest recommendation is "ship the flat policy and kill the risk engine" |
| **Limitation** | Three arms need ~1.5–2× the sample; risk of underpowering the comparison that matters most |

### H11 — Prepaid payment failures are a material cause of COD selection
| | |
|---|---|
| **Why true** | Observable in-session behaviour: attempt prepaid → fail → switch to COD. A "COD preference" that is actually a reliability defect |
| **Why false** | Failures may be too rare to matter at scale |
| **Data** | `payment_page_reached`, `payment_success`, in-session method-switch events, `payment_failure_reason` |
| **Metric** | % of COD orders preceded by a failed prepaid attempt; COD share by customer's historical payment-failure rate |
| **Analysis** | Session-level sequence analysis |
| **Implication** | **If this is large, it reframes the entire project** — the top intervention becomes payment reliability (retries, fallback rails, smart routing), not incentives or fees. Zero conversion risk, zero margin cost |
| **Limitation** | Needs session-level event data, which Phase 2 must generate deliberately |

### H12 — RTO risk is predictable pre-checkout with acceptable accuracy
| | |
|---|---|
| **Why true** | Historical behaviour, geography and order composition carry real signal |
| **Why false** | May be dominated by unobservable, order-specific noise (customer's mood on delivery day, courier quality) |
| **Data** | All pre-checkout features only |
| **Metric** | AUC, calibration curve, lift in top decile, precision at the intervention threshold |
| **Analysis** | Logistic regression / scorecard, time-based validation split |
| **Implication** | **Feasibility gate for the entire product.** If AUC < 0.65, risk-based pricing is not viable and we should ship a simple rules-based tier instead |
| **Limitation** | Synthetic data has a known DGP → AUC will be *optimistically* high. Must state this explicitly and discount expectations for real data |

**Pre-registered priors** (write these down now, compare after analysis — this is what makes it a real investigation):

| Hypothesis | Prior confidence | Expected effect size |
|---|---|---|
| H1 (raw) | 95% | RTO gap ≥15pp |
| H1 (adjusted) | 65% | gap shrinks by 30–50% but survives |
| H2 | 85% | +12–18pp COD for new |
| H3 | 90% | 2.0–2.5× lift |
| H4 | 60% | inverted-U, not monotonic |
| H5 | 55% | small but significant |
| H6 | 50% | delay > promise |
| H7 | 45% | +2–4pp prepaid |
| H8 | 80% (that it hurts CM if untargeted) | negative CM if flat |
| H9 | 70% (that conversion loss is material) | −3 to −6% net conversion |
| H10 | 70% | targeted beats flat by 1.5–2× |
| H11 | 60% | 8–15% of COD preceded by failure |
| H12 | 75% | AUC 0.72–0.80 on synthetic |

> **Interview point:** "I wrote down what I expected before I ran the analysis. Two of my priors were wrong — [fill in after Phase 5] — and that changed the recommendation." Nothing signals genuine analytical work more than a documented wrong prior.

---

## 5. METRICS TREE

### 5.1 The hierarchy

```
                    BUSINESS OUTCOME
        ┌───────────────────────────────────────┐
        │   NORTH STAR                          │
        │   Contribution Margin per             │
        │   Checkout Session Started (CM/CSS)   │
        └───────────────────┬───────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        │                                       │
   ┌────▼─────────────┐              ┌──────────▼─────────┐
   │  DEMAND CAPTURE  │              │  VALUE RETENTION   │
   │  Net Conversion  │              │  CM per Delivered  │
   │  = delivered /   │              │  Order             │
   │    sessions      │              │                    │
   └────┬─────────────┘              └──────────┬─────────┘
        │                                       │
   ┌────┴──────────────┐              ┌─────────┴──────────┐
   │ PRIMARY           │              │ PRIMARY            │
   │ • Checkout conv.  │              │ • RTO rate         │
   │   (order/session) │              │ • Contribution     │
   │ • Payment success │              │   margin/order     │
   └────┬──────────────┘              └─────────┬──────────┘
        │                                       │
   ┌────┴──────────────┐              ┌─────────┴──────────┐
   │ SECONDARY         │              │ SECONDARY          │
   │ • Address compl.  │              │ • Delivery rate    │
   │ • Payment page    │              │ • Cancellation rate│
   │   reach rate      │              │ • COD adoption     │
   │ • AOV             │              │ • Prepaid adoption │
   │ • Sessions        │              │ • Delivery attempts│
   └────┬──────────────┘              └─────────┬──────────┘
        │                                       │
   ┌────┴───────────────────────────────────────┴─────────┐
   │ DIAGNOSTIC (segment cuts — never optimised directly)  │
   │ by risk tier · tenure · geo tier · category ·         │
   │ order-value band · seller-rating band · promise-days  │
   └───────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────┐
   │ GUARDRAILS (must not breach — veto power over launch) │
   │ checkout abandonment · payment failure rate ·         │
   │ complaint rate · refund rate · 30-day repeat purchase │
   │ · CSAT proxy · low-risk-segment conversion            │
   └───────────────────────────────────────────────────────┘
```

### 5.2 The identity that connects everything

The whole project reduces to one decomposition. Memorise it — this is the answer to "walk me through your metric framework."

$$\frac{CM}{\text{Session}} = \underbrace{\frac{\text{Orders}}{\text{Sessions}}}_{\text{checkout conversion}} \times \underbrace{\left(1 - \text{RTO rate}\right)}_{\text{delivery success}} \times \underbrace{\frac{CM_{\text{delivered}}}{\text{Delivered order}}}_{\text{unit margin}} - \underbrace{\frac{\text{RTO cost} \times \text{RTO orders}}{\text{Sessions}}}_{\text{failure drag}}$$

Every intervention in §10 moves at least two of these four terms, and usually in opposite directions. That is the entire product problem in one line.

### 5.3 Metric definitions (locked — Phase 2 must generate the events these need)

**Funnel**

| Metric | Formula | Notes |
|---|---|---|
| Checkout sessions started | count(`checkout_started`) | Denominator of the north star |
| Address completion rate | `address_completed` / `checkout_started` | |
| Payment page reach rate | `payment_page_reached` / `address_completed` | Where fee reveal happens — watch for drop |
| Payment method selection rate | `(cod_selected + prepaid_selected)` / `payment_page_reached` | |
| Payment success rate | `payment_success` / prepaid attempts | Prepaid-only; COD has no analogue → **never blend these** |
| Checkout conversion | orders / `checkout_started` | The *old* conversion metric — kept as guardrail |
| **Net conversion** | delivered orders / `checkout_started` | The *real* conversion metric |
| COD selection rate | COD orders / orders | |
| Prepaid selection rate | prepaid orders / orders | |
| Abandonment rate | 1 − checkout conversion | Guardrail |

**Fulfilment**

| Metric | Formula |
|---|---|
| Delivery rate | delivered / orders shipped |
| Cancellation rate | cancelled / orders placed (split pre-ship vs post-ship, and customer vs seller actor) |
| **RTO rate** | `rto_flag=1` / orders **shipped** (not orders placed — cancellations must be excluded or the metric is not comparable across arms) |
| RTO rate by method / segment / category / geo / value band | same, sliced |
| Delivery attempts | mean attempts per shipped order |

> **Definitional trap:** if the denominator of RTO rate is "orders placed," then an intervention that increases pre-ship cancellations will *appear* to reduce RTO while doing nothing. Lock the denominator to **shipped orders** and report cancellation separately.

**Economics** — see §6.

**Guardrails**

| Guardrail | Rationale | Provisional threshold [A] |
|---|---|---|
| Checkout conversion | Catches demand destruction | ≥ −1.0% relative |
| **Low-risk segment conversion** | Catches collateral damage to good customers | ≥ −0.3% relative (tighter) |
| Payment failure rate | Catches technical regressions | ≤ +0.2pp absolute |
| Complaint rate | Catches fee backlash | ≤ +5% relative |
| Refund rate | Catches downstream dissatisfaction | ≤ +5% relative |
| 30-day repeat purchase | Catches retention damage | ≥ −1.0% relative |
| Prepaid-share among *already-prepaid* users | Detects incentive leakage (paying for nothing) | monitored, no threshold |

> **Interview point:** most candidates list guardrails as a formality. The one that actually matters here is **low-risk segment conversion**, because the whole risk-based thesis is "concentrate friction on risky orders." If low-risk conversion moves at all, the targeting is leaking and the thesis is broken. That guardrail is tighter than the aggregate one *on purpose*.

---

## 6. UNIT ECONOMICS

### 6.1 Model choice [A]

We model BharatKart as a **managed marketplace where the platform owns the unit economics** (i.e. COGS sits on our P&L). This matches the formula you specified. A pure-3P take-rate variant is shown in §6.7 because a good interviewer will ask which model you assumed and why it matters.

### 6.2 Definitions

| Term | Formula | Notes |
|---|---|---|
| **GMV** | `product_price × qty` (pre-discount) | Vanity metric — reported, never optimised |
| **Net Revenue** | `GMV − discount + shipping_fee_charged + cod_fee_charged` | Only recognised **on successful delivery** |
| **COGS** | procurement/seller payout for goods | Reversed on RTO **less shrink** |
| **Forward shipping cost** | freight to customer | Paid regardless of outcome |
| **Reverse shipping cost** | freight back to origin | Paid **only** on RTO/return |
| **Payment processing fee** | `prepaid_amount × pg_rate` | Prepaid only |
| **COD handling cost** | `cod_amount × cod_rate + fixed_attempt_fee` | COD only; attempt fee incurred even if collection fails |
| **Packaging & fulfilment** | pick/pack/material | Paid regardless of outcome |
| **Discount cost** | platform-funded promo + prepaid incentive | Paid on order placement (incentive) or on delivery (promo) |
| **Reverse handling** | re-inward, QC, restock | RTO only |
| **Shrink allowance** | `shrink_rate × COGS` | RTO only — share of returned units unsaleable at full price |
| **Support/ops cost** | NDR handling, calls | Higher on RTO |
| **Working capital carry** | `COGS × annual_rate × days_blocked / 365` | RTO only |
| **Contribution Margin** | Net Revenue − all variable costs above | **Negative on every RTO order** |

$$CM = \text{NetRev} - \text{COGS} - \text{Fwd} - \text{Rev} - \text{PG} - \text{CODH} - \text{Pack} - \text{Disc} - \text{RevHandling} - \text{Shrink} - \text{Support} - \text{WC}$$

### 6.3 Which costs are incurred, when

| Cost | Prepaid placed, delivered | COD placed, delivered | COD → RTO | Pre-ship cancel |
|---|:---:|:---:|:---:|:---:|
| Net revenue recognised | ✅ | ✅ | ❌ | ❌ |
| COGS | ✅ | ✅ | reversed (less shrink) | ❌ |
| Forward shipping | ✅ | ✅ | ✅ **wasted** | ❌ |
| Packaging | ✅ | ✅ | ✅ **wasted** | partial |
| Payment processing | ✅ | ❌ | ❌ (refund fee if incentive paid) | refund cost |
| COD handling | ❌ | ✅ | ✅ **attempt fee, no collection** | ❌ |
| Reverse shipping | ❌ | ❌ | ✅ | ❌ |
| Reverse handling / QC | ❌ | ❌ | ✅ | ❌ |
| Shrink allowance | ❌ | ❌ | ✅ | ❌ |
| Support / NDR | minimal | minimal | ✅ | minimal |
| Working capital carry | ❌ | ❌ | ✅ | ❌ |
| **Contribution margin** | **positive** | **positive, slightly lower** | **strongly negative** | **small negative** |

### 6.4 Parameter registry [A] — the single source of truth

Every one of these is an assumption. They live in `config/params.yaml` in Phase 2 so that **one file change re-runs the entire model.** This is what makes the project defensible: an interviewer can challenge any input and you can show the sensitivity in seconds.

| Parameter | Value | Basis |
|---|---|---|
| AOV (order value) | ₹1,000 | Calibration to Indian horizontal marketplace |
| Platform-funded discount | 8.0% of GMV | |
| COGS as % of net revenue | 75% | ⇒ 25% gross margin |
| Forward shipping | ₹78 | |
| Reverse shipping | ₹85 | Reverse legs cost more (low density) |
| Packaging + pick/pack | ₹12 | |
| PG fee (prepaid) | 1.8% of order value | |
| COD handling | 1.5% + ₹8 fixed | |
| COD failed-attempt fee | ₹14 | Incurred on RTO |
| Reverse handling / QC / restock | ₹35 | RTO only |
| Shrink rate on RTO units | 8% of COGS | ⇒ ~₹55 on a ₹1,000 order |
| Support / NDR cost per RTO | ₹18 | |
| Working capital rate / days blocked | 14% p.a. / 30 days | ⇒ ~₹12 |
| Ops/CS allocation per delivered order | ₹10 | |
| COD share | 62% | |
| COD RTO rate | 24.0% | |
| Prepaid RTO rate | 4.1% | |
| Blended RTO rate | **16.5%** | 0.62×24.0 + 0.38×4.1 |
| Annual orders (population) | 24,000,000 | |
| Sample → annual factor | **×240** | ×20 (5% sample→month) × 12 (month→year) |

### 6.5 Worked example — ₹1,000 order value

**Revenue side (identical for both methods):**
```
GMV                                   ₹1,000
− platform discount (8%)                 ₹80
= Net Revenue                           ₹920
```

#### A) Prepaid order — successfully delivered

```
Net Revenue                             ₹920.0
− COGS (75% of net rev)                 ₹690.0
− Forward shipping                       ₹78.0
− Packaging / pick-pack                  ₹12.0
− Payment processing (1.8% × 1,000)      ₹18.0
− Ops / CS allocation                    ₹10.0
─────────────────────────────────────────────
CONTRIBUTION MARGIN                     ₹112.0
CM % of net revenue                       12.2%
CM % of GMV                               11.2%
```

#### B) COD order — successfully delivered

```
Net Revenue                             ₹920.0
− COGS                                  ₹690.0
− Forward shipping                       ₹78.0
− Packaging / pick-pack                  ₹12.0
− COD handling (1.5% × 1,000 + ₹8)       ₹23.0
− Ops / CS allocation                    ₹10.0
─────────────────────────────────────────────
CONTRIBUTION MARGIN                     ₹107.0
CM % of net revenue                       11.6%
```

**Delta: COD delivered earns ₹5 less than prepaid delivered.** [D]

> This ₹5 is *not* the COD problem. If ₹5 were the whole story, COD would be a rounding error. The problem is entirely in the tail: 24% of these orders never become (B) at all.

#### C) COD order → RTO

```
Net Revenue                               ₹0.0     ← nothing collected
COGS                                      ₹0.0     ← goods return to stock
Costs incurred and unrecovered:
  Forward shipping                       ₹78.0
  Reverse shipping                       ₹85.0
  Packaging (destroyed)                  ₹12.0
  COD failed-attempt fee                 ₹14.0
  Reverse handling / QC / restock        ₹35.0
  Shrink allowance (8% × ₹690)           ₹55.2
  Support / NDR handling                 ₹18.0
  Working capital carry (14% × 30d)      ₹11.8
─────────────────────────────────────────────
DIRECT CASH LOSS                        ₹309.0
+ Foregone contribution margin          ₹107.0     ← opportunity cost
─────────────────────────────────────────────
TOTAL ECONOMIC IMPACT PER RTO           ₹416.0
```

### 6.6 The number that reframes the entire business case

```
COD delivered order  ..............  +₹107 CM
COD order that RTOs  ..............  −₹309 cash  (−₹416 economic)
─────────────────────────────────────────────────
Swing per order .....................  ₹416
Breakeven: it takes 2.9 successful COD deliveries
to pay for one RTO.
```

**Expected value of a COD order at 24% RTO:**
`0.76 × ₹107 + 0.24 × (−₹309) = ₹81.3 − ₹74.2 = ` **`₹7.1`**

**Expected value of a prepaid order at 4.1% RTO:**
`0.959 × ₹112 + 0.041 × (−₹309) = ₹107.4 − ₹12.7 = ` **`₹94.7`**

> **A COD order is worth ₹7. A prepaid order is worth ₹95. A COD order is worth 7.5% of a prepaid order.** [D]

**This single line is the business case.** It also immediately tells you the economics of every intervention:

- **How much can we afford to pay to convert a COD order to prepaid?** Up to **₹87.6** before it's value-destroying. A ₹30 prepaid cashback is trivially affordable — *if* it is incremental.
- **How many prepaid orders can we afford to lose to prevent one COD order?** At the margin, blocking a COD order forgoes ₹7.1. Blocking is cheap *only* at high risk tiers.
- **At what RTO probability does a COD order become value-negative?** Solve `p` in `(1−p)×107 − p×309 = 0` → **p\* = 25.7%**. Above ~26% predicted RTO, a COD order destroys value on average. **This is not an arbitrary threshold — it is the economically derived cut-line for the high-risk tier in §10.**

> **Interview point:** "My high-risk threshold isn't a percentile I picked. It's the break-even RTO probability where expected contribution margin on a COD order crosses zero: 25.7%. Above it, the order is worth less than not having it. That's how the risk tiers are defined."

### 6.7 3P take-rate variant [A]

If BharatKart were pure-3P (commission model, seller owns COGS):
- Net revenue per order = take rate (say 12%) × GMV = ₹120 on a ₹1,000 order, less payment/COD cost
- CM per delivered order ≈ ₹120 − ₹18 PG − ₹10 ops ≈ ₹92 (platform bears no COGS or freight if seller-shipped)
- But on platform-shipped orders, the platform still bears both freight legs on an RTO
- **RTO impact per order shrinks to roughly ₹180–₹230**, so the annual opportunity roughly halves

**Why this matters:** it changes who should pay for the fix. In 1P/managed, the platform captures the full ₹416 saving. In 3P, much of it accrues to the seller — so the intervention needs a **seller-cost-sharing model** or the platform's ROI is far weaker. State the model you chose; a strong interviewer will probe this.

---

## 7. THE ₹165 CRORE OPPORTUNITY FRAMEWORK

### 7.1 Four different numbers that people wrongly call "the opportunity"

This is the most rigorous part of the case study, and where most portfolio projects collapse.

| # | Name | Definition | Character | Magnitude here |
|---|---|---|---|---|
| 1 | **RTO cash cost** | Out-of-pocket variable cost on RTO orders | Real cash out the door | ₹309/RTO |
| 2 | **RTO revenue loss** | Net revenue never recognised because the order failed | Not incremental — some would never have converted | ₹920/RTO (GMV ₹1,000) |
| 3 | **RTO economic cost / total opportunity** | Cash cost + foregone contribution margin | **The correct total exposure** | ₹416/RTO |
| 4 | **Avoidable opportunity** | Portion of (3) attributable to causes a checkout intervention can plausibly influence | Requires a causal claim | ~65% of (3) [A] |
| 5 | **Recoverable opportunity** | Portion of (4) an intervention actually captures, given realistic efficacy | Requires an effect-size estimate | ~30% of (4) [A] |
| 6 | **Net incremental CM opportunity** | (5) minus intervention cost minus conversion loss | **The only number a CFO will fund against** | see waterfall |

**Never quote (2) as an opportunity.** ₹920 × RTO orders is a huge, meaningless number — it assumes every failed order would otherwise have delivered *and* treats revenue as if it were margin. This is the single most common inflation error in e-commerce cases.

**Never quote (5) as if it were (3), or (3) as if it were (6).** The resume bullet says *"modelled a ₹165 Cr annual RTO opportunity"* — that is **(3), total exposure.** Say so explicitly in the interview, then immediately show the funnel down to (6). That sequence is what demonstrates rigour.

### 7.2 The waterfall

**Level 1 — from sample to population**

```
Analytical dataset                              100,000 orders   [S]
× 20   (dataset is a 5% random sample of one month)
= Monthly orders                              2,000,000 orders   [A]
× 12
= Annual orders                              24,000,000 orders   [A]
Annualization factor                                     ×240
```

**Level 2 — orders to RTO cost**

```
Annual orders                                24,000,000          [A]
├── COD (62%)                                14,880,000          [A]
│     × COD RTO rate 24.0%          →  3,571,200 RTO orders      [A]
└── Prepaid (38%)                             9,120,000          [A]
      × Prepaid RTO rate 4.1%       →    373,920 RTO orders      [A]
                                     ─────────────────────────
Total RTO orders                            3,945,120            [D]
Blended RTO rate                                 16.44%          [D]

× Economic cost per RTO                        ₹416              [A→D, §6.5]
─────────────────────────────────────────────────────────────
TOTAL ANNUAL RTO OPPORTUNITY (exposure)     ₹164.1 Cr    ≈ ₹165 Cr   [D]
    of which cash cost (₹309)                ₹121.9 Cr
    of which foregone CM (₹107)               ₹42.2 Cr
```

**Sanity check:** ₹164 Cr ÷ ₹2,400 Cr GMV = **6.8% of GMV**. Cash-only portion = 5.1% of GMV. Both sit inside the commonly cited 4–8% band for COD-heavy Indian marketplaces. The number is not an outlier — that is the check that makes it credible.

**Cross-check from the sample directly:**
`100,000 × 16.44% = 16,440 RTO orders × ₹416 = ₹68.4 lakh × 240 = ₹164.1 Cr` ✅

**Level 3 — exposure to fundable value**

```
TOTAL ANNUAL RTO OPPORTUNITY                          ₹164.1 Cr   [D]

− Structurally unavoidable (35%) [A]                  −₹57.4 Cr
    · genuine address failures / pincode serviceability
    · courier operational failures
    · legitimate change-of-mind protected by policy
    · customer emergencies, travel, unreachable
  ─────────────────────────────────────────────────────────────
= AVOIDABLE OPPORTUNITY (65%)                         ₹106.7 Cr   [D]
    influenceable by: payment-method shift, trust signals,
    partial payment, intent screening, promise accuracy

× Realistic intervention efficacy 30% [A]
    (i.e. we capture 30% of the avoidable pool in year 1)
  ─────────────────────────────────────────────────────────────
= RECOVERABLE OPPORTUNITY                              ₹32.0 Cr   [D]

− Prepaid incentive cost [A]                           −₹6.8 Cr
− CM lost to conversion decline from fees/friction [A] −₹4.9 Cr
− Engineering + ops run cost [A]                       −₹1.5 Cr
  ─────────────────────────────────────────────────────────────
= NET INCREMENTAL CONTRIBUTION MARGIN (Yr 1)          ₹18.8 Cr   [D]
    = 0.78% of GMV
    = ₹7.8 per checkout session [D]
```

### 7.3 Sensitivity — what breaks the number

An interviewer will attack the inputs. Have this table ready.

| Input | Base | Downside | Upside | ₹ impact on the ₹164 Cr |
|---|---|---|---|---|
| Annual orders | 24M | 12M | 36M | ₹82 Cr / ₹246 Cr — **the dominant driver** |
| Cost per RTO | ₹416 | ₹300 | ₹520 | ₹118 Cr / ₹205 Cr |
| COD RTO rate | 24% | 18% | 30% | ₹128 Cr / ₹200 Cr |
| COD share | 62% | 50% | 70% | ₹138 Cr / ₹181 Cr |
| Avoidable % | 65% | 45% | 75% | affects ₹107 Cr, not headline |
| Efficacy | 30% | 15% | 45% | affects ₹32 Cr, not headline |

**Honest statement to make out loud:** *"The ₹165 Cr headline is ~80% determined by two assumptions I chose — annual order volume and cost per RTO. It is a scale-sensitive exposure figure, not a precision estimate. What is genuinely robust is the **unit economics**: a COD order is worth ₹7 and a prepaid order is worth ₹95, and that ratio holds across the entire sensitivity range. That ratio is what the product decision rests on."*

### 7.4 What we still need to test before ₹165 Cr is defensible

| Assumption | How Phase 5 tests it | If it fails |
|---|---|---|
| RTO cost per order = ₹416 | Recompute bottom-up from the simulated cost model per order, don't assume a constant | Rebuild from the empirical distribution; the constant will over-state for low-AOV orders |
| 35% unavoidable | Decompose by `return_reason`; classify each reason as addressable / not | If unavoidable > 50%, the recoverable pool halves and the project may not clear the funding bar |
| 30% efficacy | **Must come from the experiment, not assumption.** Until then it is a placeholder | Replace with the measured ATE; recompute the waterfall live |
| COD→RTO relationship is partly causal | H1 adjusted analysis + partial-payment experiment | If the gap is pure selection, payment-method interventions are worthless and the answer is customer-level risk gating |

> **Interview point:** "I built the waterfall so that the top is arithmetic, the middle is an assumption I flagged, and the bottom is something only an experiment can fill in. When someone challenges the ₹165 Cr, I don't defend the number — I show which layer they're challenging and what evidence would move it."

---

## 8. CUSTOMER SEGMENTATION FRAMEWORK

### 8.1 The critical distinction: three different segmentations for three different jobs

The most common mistake is building one segmentation and using it everywhere. They have different requirements:

| Purpose | Requirement | Type | Update cadence | Consumer |
|---|---|---|---|---|
| **Analytics** | Interpretable, stable, MECE, explains variance | Descriptive, few dimensions | Monthly | Dashboards, exec narrative |
| **Product intervention** | Actionable, few tiers, explainable to a customer, fair | Prescriptive, 3–4 tiers | Real-time | Checkout UI, pricing engine |
| **Risk scoring** | Predictive accuracy, calibrated probability, no leakage | Continuous score 0–1 | Real-time | Decision engine input |

**Relationship:** risk scoring produces a *continuous* probability → intervention segmentation is a *thresholded* version of it (using the economically derived cut-lines from §6.6) → analytics segmentation is a *separate*, descriptive lens used to explain *why* the tiers look the way they do.

### 8.2 Analytics segmentation (descriptive)

Primary lens — **Tenure × COD behaviour**, a 3×3 that maps to a real customer story:

| | Low COD (<25%) | Mixed COD (25–75%) | High COD (>75%) |
|---|---|---|---|
| **New** (0 orders) | Prepaid-native newcomer | Exploring | **Cash-first newcomer** ← highest volume, highest risk [A] |
| **Growing** (1–5) | Converting well | Undecided | **Habitual COD** |
| **Established** (6+) | Loyal prepaid — protect at all costs | Situational COD | Loyal cash customer — **do not penalise** |

Secondary lenses (cross-cut, not combined into a mega-segment): geography tier, order-value band, category affinity, purchase frequency, historical RTO band.

> **Design rule:** do not create a 5×4×3×3 grid. 180 cells that nobody can act on is not segmentation, it's a pivot table. Two dimensions for the narrative, everything else as a filter.

### 8.3 Intervention segmentation (prescriptive)

Three tiers, thresholds derived from §6.6 economics (**not** percentiles):

| Tier | Definition | Economic meaning | Expected share [A] |
|---|---|---|---|
| **Low risk** | P(RTO) < 10% | COD order EV ≈ +₹65. Comfortably profitable. **Any friction here is pure loss** | ~45% |
| **Medium risk** | 10% ≤ P(RTO) < 25.7% | EV between +₹65 and ₹0. Profitable but thin. Worth *nudging*, not *charging* | ~38% |
| **High risk** | P(RTO) ≥ 25.7% | EV ≤ ₹0. **The order destroys value as-is.** Justifies fees, partial payment, or gating | ~17% |

**The bright line at 25.7%** is `p*` where `(1−p)×107 − p×309 = 0`. Everything above it is an order we'd rather not have in its current form.

> **Interview point:** "Percentile-based tiers (top 20% = high risk) are arbitrary and drift with the population. Economically derived thresholds are stable and defensible: at 25.7% predicted RTO, the order's expected contribution margin is exactly zero. That number came from the cost model, not from a slider."

### 8.4 The fairness overlay — a non-negotiable constraint

A pure risk score will penalise customers for things they cannot control (living in a Tier-3 pincode) and will create a **new-customer trap**: no history → high risk → friction → worse first experience → churn → never builds history.

Three protections, defined now and enforced in §10 and in the PRD:

1. **Never take away a payment option from a customer with a clean record.** Tenure ≥3 delivered orders and zero prior RTO ⇒ hard-capped at Medium tier, regardless of score.
2. **New customers get carrots, never sticks.** No-history customers are eligible for prepaid incentives and trust messaging, but never for COD fees or COD restrictions. They have no track record to be penalised for.
3. **Geography can inform the score but must not be the *sole* driver of a restriction.** Audit: measure the intervention rate by geo tier; if Tier-3 restriction rate is >2.5× Metro, the model is proxying for postcode, not behaviour. Escalate.

> **Interview point:** "A risk model in a consumer product is a *policy*, not just a classifier. I wrote the fairness constraints before I wrote the model, because after you have the AUC it's very hard to argue yourself out of using it."

### 8.5 Which segmentation feeds which artefact

| Artefact | Segmentation used |
|---|---|
| Executive dashboard | Analytics (tenure × COD) |
| Risk-tier P&L | Intervention (3 tiers) |
| Checkout decision engine | Continuous risk score + fairness overlay |
| Experiment stratification | Intervention tiers (to guarantee balance and enable CATE) |
| RTO diagnosis / root cause | Analytics + geo/category cross-cuts |

---

## 9. RISK FRAMEWORK (conceptual — no model built yet)

### 9.1 The four questions every feature must pass

Before a feature enters the model it must answer all four. Most portfolio projects skip questions 2 and 3, which is exactly where real risk models die.

1. **Availability** — is it known *before the customer selects a payment method*? (not before delivery — before *checkout*)
2. **Leakage** — does it encode the outcome? Anything downstream of shipment is disqualified.
3. **Real-time feasibility** — can it be computed in <100ms at checkout, or does it require a batch join?
4. **Actionability** — if this feature is high, does it change what we *do*? A feature that predicts but doesn't inform any decision is a dashboard metric, not a model input.

### 9.2 Feature audit

| Feature | Why it matters | Available pre-checkout? | Leakage risk | Real-time? | Actionable? | Verdict |
|---|---|---|---|---|---|---|
| `historical_rto_rate` | Strongest behavioural signal; past failure predicts future failure | ✅ | ⚠️ must be strictly time-lagged (exclude current order) | ✅ precomputed | ✅ tier assignment | **INCLUDE** — with shrinkage for low-n |
| `historical_cod_share` | Habit + revealed payment preference | ✅ | ✅ none | ✅ | ✅ | **INCLUDE** |
| `historical_prepaid_success_count` | Direct evidence of platform trust already established | ✅ | ✅ | ✅ | ✅ strongest "protect this customer" signal | **INCLUDE — high weight** |
| `customer_tenure_days` | Proxy for relationship depth | ✅ | ✅ | ✅ | ⚠️ not directly changeable | **INCLUDE** |
| `historical_orders` (delivered) | Track record volume; stabilises the rate features | ✅ | ✅ | ✅ | ✅ | **INCLUDE** |
| `order_value` | Loss magnitude *and* behavioural anxiety driver | ✅ | ✅ | ✅ | ✅ drives fee/partial-payment sizing | **INCLUDE** |
| `product_category` | Fit/size uncertainty varies hugely by category | ✅ | ✅ | ✅ | ✅ | **INCLUDE** |
| `seller_rating` | Fulfilment reliability + trust proxy | ✅ | ✅ | ✅ | ✅ also a seller-lever | **INCLUDE** |
| `product_rating` | Product-level trust proxy | ✅ | ✅ | ✅ | ✅ | **INCLUDE** |
| `review_count` | Confidence/thinness of the rating signal | ✅ | ✅ | ✅ | ⚠️ | **INCLUDE** (interacts with rating) |
| `estimated_delivery_days` | Intent decay window | ✅ known at checkout | ✅ | ✅ | ✅ can be improved via fulfilment routing | **INCLUDE** |
| `delivery_pincode_tier` | Serviceability + address quality | ✅ | ✅ | ✅ | ⚠️ **fairness risk — monitor** | **INCLUDE with audit** |
| `discount_pct` | Deal-seeking / low-commitment proxy | ✅ | ✅ | ✅ | ✅ | **INCLUDE** |
| `address_completeness_score` | Poor address is a direct RTO mechanism | ✅ computed at address step | ✅ | ✅ | ✅ → prompt for correction, cheapest fix of all | **INCLUDE** |
| `payment_failure_history` | Tests H11 — is COD a symptom of broken rails? | ✅ | ✅ | ✅ | ✅ → route to a working rail | **INCLUDE** |
| `hour_of_day` / `day_of_month` | Impulse timing; salary-cycle liquidity | ✅ | ✅ | ✅ | ⚠️ weak | **OPTIONAL** |
| `cart_size` / items | Multi-variant ordering = optionality-seeking | ✅ | ✅ | ✅ | ✅ | **INCLUDE** |
| `delivery_attempts` | — | ❌ post-shipment | ❌ **SEVERE LEAKAGE** | — | — | **EXCLUDE** |
| `actual_delivery_days` | — | ❌ | ❌ **SEVERE LEAKAGE** | — | — | **EXCLUDE** |
| `return_reason` | — | ❌ | ❌ **SEVERE LEAKAGE** | — | — | **EXCLUDE** |
| `order_cancelled` | — | ❌ | ❌ **SEVERE LEAKAGE** | — | — | **EXCLUDE** |
| `customer_lifetime_value` | — | ✅ | ⚠️ **partially computed from delivered outcomes → subtle leakage** | ✅ | ✅ | **EXCLUDE from model; USE in the fairness/value overlay** |

> **Interview point:** "The four features I excluded for leakage would have pushed AUC to ~0.95. That model would be useless — it would only be able to tell me an order failed *after* it failed. My real constraint isn't accuracy, it's that every feature must exist at the moment the customer taps 'Place Order'."

### 9.3 Model approach (Phase 7)

**Start interpretable. Escalate only if the escalation pays for itself.**

1. **Baseline: rules.** Payment method + prior RTO + tenure. Three rules. Establishes the floor any model must beat.
2. **Primary: logistic regression → scorecard.** Coefficients convert to points; the score is explainable to a customer-support agent, a risk committee, and a regulator. Well-calibrated probabilities, which we *need* because thresholds are economic (25.7%), not rank-based.
3. **Challenger: gradient-boosted trees.** Only shipped if it beats the scorecard by ≥3pp AUC **and** ≥₹X in simulated CM. Otherwise the interpretability is worth more than the accuracy.

**Calibration is more important than AUC here.** A model with AUC 0.78 and good calibration is more useful than AUC 0.83 with poor calibration, because we threshold on an absolute probability tied to money. Report a reliability curve, not just AUC.

**Validation:** time-based split (train on earlier weeks, test on later), never random — random splits leak temporal structure and inflate performance.

**Honest caveat to state up front:** synthetic data has a known DGP, so the model recovers relationships that were planted. AUC will be flattering. The *right* claim is "the pipeline recovers the planted structure," not "this model would get 0.80 in production."

### 9.4 Feasibility gate

| Outcome | Decision |
|---|---|
| AUC ≥ 0.72, well calibrated | Proceed with full risk-based pricing |
| AUC 0.65–0.72 | Proceed with **coarse tiers only** — no fine-grained fee ladders |
| AUC < 0.65 | **Kill the risk engine.** Recommend flat trust intervention + payment reliability fixes. Report this honestly |

> The willingness to pre-commit to killing your own headline feature is the strongest signal of product judgment in this entire document.

---

## 10. INTERVENTION LIBRARY

All impact figures below are **[A] illustrative priors derived from the §6 economics** — they are what we *expect*, written down before the experiment so we can be wrong in public. None are results.

### 10.1 The interventions

#### A — Prepaid incentive (cashback/discount for paying online)

| | |
|---|---|
| **Target segment** | Medium & high risk, COD-inclined, **with an available prepaid instrument** |
| **User problem** | "I'd pay online but there's no reason to — COD costs me nothing and gives me an option" |
| **Mechanism** | ₹25–50 instant discount or wallet cashback shown *at the payment step*, framed as a reward not a penalty |
| **Expected behaviour change** | +15–25pp prepaid share within the targeted cohort |
| **RTO impact** | Strong on shifted orders (24% → 4.1%). Zero on non-shifted |
| **Conversion impact** | Neutral to slightly positive (it's a discount) |
| **CM impact** | **Depends entirely on targeting** — see 10.2. Flat = negative. Targeted = positive |
| **Risks** | Incentive leakage to already-prepaid users; discount habituation; margin erosion becomes permanent expectation |
| **Experiment needed** | Yes. Must include a **flat-offer arm** and a **targeted arm** to measure the targeting premium (H10) |

#### B — COD fee

| | |
|---|---|
| **Target segment** | **High risk only** (P(RTO) ≥ 25.7% — where COD EV is already negative) |
| **User problem** | None. This is a business intervention, and we should be honest about that |
| **Mechanism** | ₹29–49 convenience fee, disclosed **at the payment step, before selection**, never at the end. Waived if the customer chooses prepaid — framed as a *choice*, not a punishment |
| **Expected behaviour change** | −20 to −30pp COD share in the targeted cohort; some outright abandonment |
| **RTO impact** | Strongest of any lever — it screens on *intent*, which nothing else does |
| **Conversion impact** | **Negative, −5 to −10% in the targeted cohort.** This is the cost |
| **CM impact** | Positive *only* where the order EV was already negative. Above p*=25.7%, an abandoned order is a **saving**, not a loss |
| **Risks** | Brand damage; fee-aversion overshoot; regulatory/marketplace-policy scrutiny; competitor migration; press risk |
| **Experiment needed** | Yes — with the tightest guardrails in the programme, and a qualitative/complaint monitor |

#### C — Trust-building checkout

| | |
|---|---|
| **Target segment** | All, weighted to new customers and low-rating/low-review products |
| **User problem** | "I don't know enough about this seller/product/delivery to commit money now" |
| **Mechanism** | At the payment step: seller rating + fulfilment record, review snapshot, firm delivery date, **"free returns / easy refund"** badge, secure-payment/escrow messaging, "your money is held until delivery" framing |
| **Expected behaviour change** | +2–4pp prepaid share; possible small conversion lift |
| **RTO impact** | Small-to-moderate, indirect |
| **Conversion impact** | Neutral or positive |
| **CM impact** | **Positive with near-zero marginal cost** — no discount, no fee, no conversion risk |
| **Risks** | May be ignored; adds visual clutter; showing low ratings honestly may *reduce* conversion on weak listings (which may be correct behaviour) |
| **Experiment needed** | Yes — this is the **only way to test H7**, since no observational variation exists |
| **Note** | **Highest expected ROI per unit of risk.** Ship this first regardless of what the risk model says |

#### D — Partial payment ("pay ₹99 now, rest on delivery")

| | |
|---|---|
| **Target segment** | High risk who refuse full prepaid |
| **User problem** | "I want to inspect before paying in full, but I'm a genuine buyer" |
| **Mechanism** | Small non-refundable-on-refusal token payment (or refundable-on-genuine-issue) locks in commitment while preserving inspection rights |
| **Expected behaviour change** | Converts a meaningful share of high-risk COD into committed orders; screens out pure optionality-seekers |
| **RTO impact** | **Potentially the largest of any lever**, because it directly attacks the zero-collateral mechanism identified in the problem tree |
| **Conversion impact** | Small negative (extra step, extra decision) |
| **CM impact** | Positive if it works — and it costs no discount |
| **Risks** | Highest build complexity (refunds, disputes, partial-settlement reconciliation, CS load); refund-policy and consumer-protection exposure; confusing to explain in one line |
| **Experiment needed** | Yes — but only after A/B/C, because build cost is high |
| **Note** | **This is the intellectually most interesting intervention and the one that best tests the core mechanism.** It is also the one most likely to be descoped. Say both things in the interview |

#### E — Smart payment recommendation

| | |
|---|---|
| **Target segment** | All |
| **Mechanism** | Reorder / pre-select / visually emphasise payment methods by risk tier. Low risk → COD stays prominent. High risk → prepaid options surfaced first with UPI one-tap |
| **Expected behaviour change** | +3–6pp prepaid via default and salience effects alone |
| **Conversion impact** | Neutral if COD remains reachable in one tap |
| **CM impact** | Positive, near-zero cost |
| **Risks** | **Dark-pattern territory.** Emphasis is acceptable; hiding or burying COD is not. Draw the line explicitly in the PRD |
| **Experiment needed** | Yes, cheap to run |

#### F — Prepaid checkout friction reduction / payment reliability

| | |
|---|---|
| **Target segment** | All, especially customers with prior payment failures |
| **User problem** | "I tried to pay online and it failed / took too long, so I picked COD" |
| **Mechanism** | UPI intent one-tap, tokenised saved instruments, smart PG routing away from failing acquirers, auto-retry with fallback rail, clear failure messaging with retry rather than silent drop to COD |
| **Expected behaviour change** | Depends entirely on H11's magnitude |
| **RTO impact** | Indirect but real |
| **Conversion impact** | **Positive** |
| **CM impact** | **Positive** |
| **Risks** | Engineering-heavy; depends on PG partners; benefit invisible if failure rates are already low |
| **Experiment needed** | Ideally a holdback, though reliability fixes are often shipped without one |
| **Note** | **The only intervention with no downside on any axis.** If H11 shows failure-driven COD is >10%, this jumps to #1 priority and partially reframes the project |

### 10.2 The arithmetic that proves risk-based targeting (H10)

This is the calculation to have on a slide. All values [A], derived from §6.

**Prepaid incentive — flat ₹30 to everyone choosing prepaid:**
```
Baseline prepaid share                         38%
Post-incentive prepaid share                   46%   (+8pp)
Incentive cost per order      = 0.46 × ₹30 = −₹13.80
RTO saving per shifted order  = (24.0% − 4.1%) × ₹416 = ₹82.80
Benefit per order             = 0.08 × ₹82.80 = +₹6.62
───────────────────────────────────────────────────────
NET CM IMPACT                                −₹7.18 / order   ✗ VALUE DESTROYING
```
**Why:** 38 of every 46 rupees is paid to customers who were already going to pay online. Incentive leakage is 83%.

**Prepaid incentive — ₹30 targeted at high-risk only (17% of orders):**
```
Within the targeted cohort:
  Baseline prepaid share                       15%
  Post-incentive prepaid share                 35%   (+20pp)
  Incentive cost      = 0.35 × ₹30           = −₹10.50
  RTO saving/shift    = (38% − 8%) × ₹416    = ₹124.80
  Benefit             = 0.20 × ₹124.80       = +₹24.96
  Net within cohort                          = +₹14.46
Scaled to all orders  = 0.17 × ₹14.46        = +₹2.46 / order  ✓ VALUE CREATING
```

**Delta between the two strategies: ₹9.64 per order × 24M orders ≈ ₹23 Cr/year, from targeting alone.**

**COD fee — ₹39 on high-risk COD orders only:**
```
Baseline EV of a high-risk COD order (38% RTO):
   0.62 × ₹107 − 0.38 × ₹309 = −₹51.10        ← already destroying value

Per 100 high-risk COD orders after a ₹39 fee:
   75 stay COD (fee collected on delivery):
        0.62 × (107+39) − 0.38 × 309 = −₹26.90  → −₹2,018
   15 switch to prepaid (RTO drops to 8%):
        0.92 × 112 − 0.08 × 309      = +₹78.30  → +₹1,175
   10 abandon:                          ₹0      →      ₹0
   ─────────────────────────────────────────────────────
   Treatment total                                −₹843
   Baseline total (100 × −51.10)                −₹5,110
   IMPROVEMENT                                  +₹4,267 per 100
                                              = +₹42.67 per high-risk COD order
```

> **The counterintuitive insight, and the single best talking point in the project:** *"The 10 orders we lost to the fee were worth −₹51 each. Losing them was worth +₹511. In the high-risk tier, conversion loss is not a cost — it's part of the benefit. That is only true above the break-even RTO probability, which is exactly why the fee must never be applied below it."*

### 10.3 Prioritisation

Scored on expected CM impact, conversion risk, build effort, and confidence. [A]

| Intervention | CM impact | Conv. risk | Build effort | Confidence | Priority |
|---|---|---|---|---|---|
| **C — Trust checkout** | Medium | Very low | Low | Medium | **1 — ship first** |
| **F — Payment reliability** | Medium-High | None (positive) | Medium | Medium (H11-dependent) | **2** |
| **E — Smart recommendation** | Medium | Low | Low | Medium | **3** |
| **A — Targeted prepaid incentive** | High | Low | Medium (needs risk engine) | Medium-High | **4** |
| **B — Targeted COD fee** | High | **High** | Medium | Medium | **5 — high-risk tier only** |
| **D — Partial payment** | Potentially highest | Medium | **High** | Low | **6 — pilot later** |

**Prioritisation logic:** ship the zero-downside levers (C, E, F) first to establish the experiment infrastructure and bank easy value; then the targeted-economics levers (A, B) once the risk engine clears its feasibility gate; then the structural bet (D) once we've learned whether commitment is really the mechanism.

---

## 11. EXPERIMENTATION STRATEGY (conceptual — no sample size yet)

### 11.1 Design

| Element | Decision | Reasoning |
|---|---|---|
| **Control** | Existing checkout: uniform payment options, no risk logic, no incentive, no fee | |
| **Treatment 1** | Flat intervention (same treatment for everyone) | **Essential.** Without it we cannot separate "the intervention works" from "targeting works" — which is the actual thesis (H10) |
| **Treatment 2** | Risk-based intervention (tier-differentiated) | The recommendation |
| **Randomisation unit** | **Customer** (not session, not order) | Three reasons: (1) a customer seeing a fee once and no fee next time is an inconsistent, confusing experience; (2) sessions from one customer are correlated → session-level randomisation understates variance and inflates significance; (3) we need to measure repeat purchase, which is a customer-level outcome |
| **Assignment** | Deterministic hash of `customer_id + experiment_salt` | Stable across sessions and devices; reproducible; no state to store |
| **Stratification** | By risk tier and geo tier | Guarantees balance in the small high-risk stratum and enables clean CATE estimation by tier |
| **Population** | All checkout sessions **except**: employees, bot/fraud-flagged, non-serviceable pincodes, and (per §8.4) COD-fee exclusion for zero-history customers | |
| **Exposure point** | Log assignment when the **payment step renders**, not at session start — otherwise the denominator is diluted with sessions that never saw the treatment | |
| **Duration** | Minimum 3 weeks + a 30-day outcome-maturation window before the final read | Must span ≥2 full weekly cycles (weekend/weekday mix differs sharply) and cross a salary cycle, since COD liquidity is month-end sensitive |

### 11.2 Metrics

| Tier | Metric | Role |
|---|---|---|
| **Primary** | `CM_realized / checkout session started` | The launch decision rests on this alone |
| **Early-read proxy** | `CM_expected / checkout session started` | Uses predicted RTO. Tells us whether to *continue*, never whether to *ship* |
| Secondary | Net conversion (delivered/session) | |
| Secondary | COD share, prepaid share | Mechanism check — did the behaviour we theorised actually happen? |
| Secondary | RTO rate (shipped denominator) | |
| Secondary | CM per delivered order, AOV | Detects margin dilution / basket shrinkage |
| Secondary | Payment success rate | |
| Guardrail | Checkout conversion (orders/session) | ≥ −1.0% rel |
| Guardrail | **Low-risk-tier conversion** | ≥ −0.3% rel — **the fairness guardrail; veto power** |
| Guardrail | Complaint rate, refund rate | ≤ +5% rel |
| Guardrail | 30-day repeat purchase | ≥ −1.0% rel |
| Guardrail | Payment failure rate | ≤ +0.2pp abs |

### 11.3 Why RTO cannot be the primary metric

Four independent reasons — an interviewer will accept any one, so lead with the strongest:

1. **Feedback lag.** RTO resolves 15–30 days after order. A 3-week experiment reading RTO as primary is reading a heavily censored, biased-early sample.
2. **It is trivially gameable.** Block all COD → RTO → ~0. A metric whose optimum destroys the business cannot be primary.
3. **It ignores the cost of achieving it.** RTO down 3pp is worthless if you paid a 6pp conversion decline and ₹30/order in incentives for it.
4. **It's a rate with a moving denominator.** If the intervention changes the composition of orders, the RTO *rate* moves even when the *count* of failures doesn't. Rates are diagnostics; money is the objective.

> RTO is the **strongest secondary metric** in this experiment and the mechanism we care most about. It is simply not the thing we optimise.

### 11.4 Reasoning about MDE (arithmetic deferred to Phase 5)

The MDE should be derived from **economics**, not chosen for statistical convenience. The logic chain:

```
Annual sessions [A]            ≈ 35.3M   (24M orders ÷ 68% checkout conversion)
Baseline CM/session [D]        ≈ ₹27.5   (₹97 Cr total CM ÷ 35.3M sessions)
Programme run cost [A]         ≈ ₹1.5 Cr/yr → ₹0.42 per session
Minimum worth shipping         ≈ ₹1.50 per session  (3.5× breakeven, buffer for effect decay)
⇒ MDE ≈ +₹1.50 / session ≈ +5.5% relative on CM/CSS
```

Three practical complications to flag now, solve in Phase 5:

- **CM/session has enormous variance.** Most sessions contribute ₹0; converters contribute +₹107 or −₹309. High CV ⇒ large sample requirement. **CUPED using pre-period customer CM will materially cut this** — it's the single highest-leverage variance reduction available, because customer-level CM is strongly autocorrelated.
- **Winsorise the tail.** A handful of ₹50,000 orders can dominate the mean. Cap at the 99th percentile and report both capped and uncapped.
- **The high-risk stratum is only ~17% of traffic**, and it's where the COD fee effect lives. Power the *stratum*, not just the aggregate — otherwise the headline reads flat and the real effect is invisible.

### 11.5 Statistical method selection (PM-friendly)

| Situation | Method | Why |
|---|---|---|
| Conversion, COD share, RTO rate (binary, two arms) | **Two-proportion z-test** | Simple, correct for rates |
| CM per session / per order (continuous) | **Welch's t-test** (unequal variance) | Never assume equal variance across arms — the treatment changes the distribution shape, not just the mean |
| Payment-method mix across 3+ arms | **Chi-square** | Tests the whole distribution at once |
| Any metric where we want to adjust for imbalance or estimate effects by tier | **Regression (OLS / logistic) with covariates** | Reduces variance, handles stratification, gives CATE by risk tier in one model |
| Reducing noise on a high-variance metric | **CUPED** using pre-experiment customer CM | Typically 20–50% variance reduction on autocorrelated metrics; equivalent to a much longer test for free |
| Continuous monitoring | **Sequential / always-valid CIs (mSPRT)** or fixed-horizon only | See below |

**When sequential testing becomes dangerous:** peeking at a fixed-horizon test and stopping at the first significant result inflates false-positive rate from 5% to 20–30%. The right options are (a) fix the horizon and only read at the end, or (b) use a genuinely sequential method with always-valid boundaries. **A hybrid — "we'll use fixed-horizon stats but stop early if it looks good" — is the worst of both.** In this project it is *especially* dangerous because RTO matures late: an early read is not just noisy, it is **systematically biased** toward orders that resolved fast. Pre-register the horizon.

> **Interview point:** "The safety monitoring and the efficacy decision are different jobs. I'll check guardrails daily and stop the test if something is on fire — that's a one-sided safety rule with a wide boundary. I will not look at the primary metric until the pre-registered horizon plus the 30-day maturation window."

---

## 12. DECISION FRAMEWORK — LAUNCH / ITERATE / KILL

### 12.1 Thresholds derived from economics, not vibes

| Threshold | Value | Derivation |
|---|---|---|
| Minimum CM/session lift to ship | **+₹1.50** | ₹1.5 Cr annual run cost ÷ 35.3M sessions = ₹0.42 breakeven; 3.5× buffer for effect decay and estimate uncertainty |
| Aggregate net-conversion floor | **−1.0% relative** | Below this, demand destruction becomes strategically material and compounds through retention |
| Low-risk-tier conversion floor | **−0.3% relative** | Tighter by design: low-risk orders have +₹65 EV, so *any* loss there is pure value destruction. The whole thesis is that we don't touch them |
| RTO improvement expected | ≥ −2.0pp absolute in treated cohorts | Below this, the mechanism didn't fire and the CM gain (if any) came from somewhere we don't understand |
| Statistical bar | 95% CI on primary excludes ₹0, power ≥80% | Standard, pre-registered |
| Maturation | ≥30 days post-last-order before final read | RTO censoring |

### 12.2 The decision table

| Decision | All conditions must hold |
|---|---|
| **🟢 LAUNCH** | ΔCM_realized/session ≥ **+₹1.50**, 95% CI excludes ₹0 **AND** net conversion ≥ −1.0% rel **AND** low-risk conversion ≥ −0.3% rel **AND** no guardrail breached **AND** ≥30d maturation **AND** the mechanism fired (COD share and RTO moved in the predicted direction) |
| **🟡 ITERATE** | Any of: CM lift positive but < ₹1.50 or CI includes ₹0 · RTO improved but conversion breached the guardrail · effect concentrated in one tier only · directionally right but underpowered · mechanism didn't fire despite CM lift (we don't understand *why* it worked → don't scale it) |
| **🔴 KILL** | Any of: ΔCM/session ≤ ₹0 with CI excluding a positive effect · low-risk conversion breached (fairness failure — **non-negotiable, kills regardless of CM**) · complaint or refund guardrail breached · repeat purchase declined ≥1% · risk model AUC <0.65 (targeting isn't real) · effect exists only in the flat arm (⇒ ship the simple flat version and kill the risk engine) |

### 12.3 Iterate paths (specify *before* the results, so the response isn't improvised)

| Observed pattern | Diagnosis | Next iteration |
|---|---|---|
| RTO ↓ strongly, conversion ↓ too much | Fee is over-calibrated | Lower fee; raise the risk threshold; test waiver-on-prepaid framing |
| CM ↑ only in high-risk tier | Effect is real but narrow | Ship for high-risk only; drop the mid-tier treatment |
| Prepaid share ↑ but CM flat | Incentive leakage — we bought customers we already had | Tighten targeting; test a lower incentive; add a propensity filter |
| Conversion flat, RTO flat, CM flat | Nobody noticed the treatment | Test salience/placement before concluding the mechanism is wrong |
| Everything improves in the flat arm too | Targeting adds no value (H10 rejected) | **Ship flat, kill the risk engine.** Report honestly |

### 12.4 Why the decision cannot be RTO-only

If we launched on RTO alone, the COD fee would launch every time — it always reduces RTO. Then:
- Conversion falls 6%
- We lose the Tier-3 growth cohort
- CM per delivered order rises, total CM falls
- The dashboard turns green and the P&L turns red

**A single-metric launch criterion in a multi-objective system is how good analysis produces bad products.** Three-metric criteria are harder to satisfy on purpose — that difficulty *is* the safeguard.

---

## 13. THE TEN HARDEST QUESTIONS — AND ANSWERS

**Q1. How do you know COD *causes* RTO rather than just correlating with it?**
> I don't, from observational data, and I say so. What I can show is: raw RTO gap of ~20pp; after adjusting for tenure, geography, order value, category and seller rating, the gap shrinks but survives — call it the association ceiling. Beyond that, payment method is *self-selected*, so there's an unobservable confounder (purchase intent) that drives both. That's precisely why the partial-payment intervention (D) is in the library: it creates **exogenous variation in prepayment** while holding the customer constant, which is the only clean way to identify the commitment mechanism. And practically — my product decision doesn't require the causal claim. It requires knowing which *orders* are risky, which is a prediction problem, and prediction doesn't need causality.

**Q2. How do you know trust is the driver of COD selection?**
> I treat it as one hypothesis among seven, including two that aren't trust at all — financial access and payment reliability. Trust isn't directly observable without survey data, so I use behavioural proxies: seller rating, product rating, review count, prior prepaid success, prior payment failures, delivery-promise length. If COD share rises as ratings fall *after* controlling for category, price, tenure and geography, that's evidence consistent with product-trust. It is not proof, and it can't distinguish product-trust from platform-trust. My resume says "trust as *a* driver," not *the* driver — and my intervention set reflects that, because it includes a payment-reliability fix that has nothing to do with trust.

**Q3. Why not just eliminate COD?**
> Because 62% of orders are COD and only ~30% of those customers would switch — so I'd lose ~40% of orders to save ₹164 Cr of RTO cost on a ₹97 Cr CM base. The arithmetic is fatal. Strategically it's worse: COD skews to Tier-2/3 and new customers, which is the growth cohort, and every competitor offers it, so it's a one-click migration. COD isn't a defect; it's a feature with a risk premium. The job is to price the premium, not ban the product.

**Q4. Why would a customer accept a COD fee?**
> Many won't — I've modelled a 5–10% conversion loss in the targeted cohort, and I've capped where it applies. It only appears above a predicted RTO probability of 25.7%, which is where the order's expected contribution margin is already negative. In that tier, the orders I lose were worth −₹51 each, so losing them is worth +₹51. The fee is framed as waivable — pay online and it disappears — so it's a choice, not a penalty. And it never applies to anyone without a track record, because you can't penalise a customer for a history they don't have.

**Q5. Why is contribution margin a better north star than conversion?**
> Because conversion counts an order that RTOs as a success, and that order costs ₹309 in cash. Optimising conversion with COD present means optimising for the production of value-destroying orders. I use *contribution margin per checkout session started* — the numerator is money we actually keep, the denominator is demand we actually exposed. You can't game it by killing demand (denominator is fixed) or by buying orders with discounts (numerator absorbs the cost). I still track conversion, as a guardrail, and I redefined it as *net* conversion — delivered orders per session.

**Q6. How do you know the intervention caused the improvement?**
> Randomised, customer-level assignment, stratified by risk tier, pre-registered primary metric and horizon, and a 30-day maturation window before the final read. I have three arms — control, flat treatment, targeted treatment — so I can separate "the intervention works" from "targeting works," which are different claims. I'll validate randomisation with a pre-period A/A on the primary metric, and I don't peek: guardrails get a one-sided daily safety check with wide boundaries, the primary metric gets read once at the pre-registered horizon.

**Q7. How do you avoid penalising good customers?**
> Three hard constraints written before the model, not after. First, any customer with ≥3 delivered orders and zero RTO history is capped at Medium tier regardless of score — we never remove a payment option from someone with a clean record. Second, new customers are only ever eligible for carrots, never sticks; otherwise you build a new-customer trap where no history means friction means churn means never building history. Third, geography can inform the score but can't be the sole basis of a restriction, and I audit intervention rates by tier — if Tier-3 gets restricted more than 2.5× Metro, the model is proxying for postcode and I escalate. And the tightest guardrail in the whole experiment is low-risk-tier conversion at −0.3%: if we touch good customers at all, the test dies.

**Q8. What happens if conversion falls?**
> It depends entirely on *whose*. Aggregate net conversion has a −1.0% relative floor. Low-risk conversion has a −0.3% floor and has veto power — breaching it kills the launch regardless of margin. But conversion falling in the high-risk tier is expected and is part of the mechanism: those orders had negative expected value. I'd rather explain a conversion decline that came with a margin increase than the reverse. The framework forces me to be explicit about which conversion I'm willing to trade.

**Q9. Why should the company invest engineering time in this?**
> ₹18.8 Cr of net incremental contribution margin in year one, against roughly ₹1.5 Cr of build-and-run cost — that's a 12× return on a ₹97 Cr CM base, so about 19% CM growth without acquiring a single new customer. And the asset outlives the project: a real-time risk-scoring and intervention layer at checkout is reusable for fraud, credit, delivery-promise personalisation and seller-quality gating. I'd also start with the zero-cost interventions — trust messaging and payment reliability — so the first release proves value before the expensive parts get built.

**Q10. How do you know the ₹165 Cr is real?**
> It isn't "real" — it's a modelled exposure on simulated data, and I'd say that in the first sentence. It's ₹416 of economic impact per RTO × 3.95M RTO orders/year, where ₹416 is nine cost lines built bottom-up plus foregone margin. The cross-check is that it lands at 6.8% of GMV, inside the 4–8% band commonly cited for COD-heavy Indian marketplaces. Two inputs drive ~80% of it — annual order volume and cost per RTO — and I show the sensitivity band openly. More importantly, ₹165 Cr is the *exposure*, not the prize: ₹107 Cr is plausibly avoidable, ₹32 Cr recoverable at realistic efficacy, and ₹18.8 Cr net of intervention cost. If someone only remembers one number from this project, I'd rather it be ₹18.8 Cr than ₹165 Cr — or better, that a COD order is worth ₹7 and a prepaid order is worth ₹95.

**Bonus — the question that catches most people: "Your dataset is synthetic. Doesn't that make all of this meaningless?"**
> It makes the *findings* meaningless and the *method* fully testable. I control the data-generating process, so I know the ground truth — which means I can verify that my analysis recovers the relationships I planted and, more usefully, that my naive analysis *doesn't*. The headline demonstration in this project is that the raw COD–RTO gap over-states the true planted effect by roughly a third because of selection. On real data I could never prove that. Here I can show exactly how much a naive read would have misled the business.

---

## 14. PROJECT BLUEPRINT

### A. One-page summary

| | |
|---|---|
| **Title** | E-commerce Checkout Optimization: Reducing RTO While Protecting Conversion and Contribution Margin |
| **Context** | Simulated Indian horizontal marketplace, 24M orders/yr, ₹2,400 Cr GMV, 62% COD |
| **Problem** | Checkout treats every customer identically at the payment step; 16.5% of shipped orders never deliver, destroying ~₹164 Cr/yr of economic value |
| **Core insight** | A COD order is worth ₹7 in expected CM; a prepaid order is worth ₹95. Above 25.7% predicted RTO, a COD order has negative expected value |
| **Thesis** | Make the payment step risk-aware — allocate incentives, fees, trust signals and payment options by predicted delivery-failure risk |
| **North star** | Contribution margin per checkout session started |
| **Approach** | Synthetic 100K-order dataset → funnel & RTO diagnostics → interpretable risk model → intervention economics → simulated 3-arm A/B → launch framework → PRD |
| **Expected outcome** | ~₹18.8 Cr net incremental CM/yr; +₹5.3 per checkout session |
| **Key risk** | H10 fails — targeting adds no value over a flat policy. Pre-committed response: ship flat, kill the risk engine |

### B. Problem statement
See §2.2 (locked).

### C. Hypothesis tree
§3 (22 nodes, tagged Hypothesis/Evidence/Data) + §4 (H1–H12 with pre-registered priors).

### D. Metrics tree
§5. North star = CM/CSS. Identity: `CM/session = conversion × (1−RTO) × unit margin − failure drag`.

### E. Unit economics framework
§6. Prepaid delivered +₹112 · COD delivered +₹107 · COD RTO −₹309 cash / −₹416 economic. Break-even p\* = 25.7%.

### F. Opportunity-sizing framework
§7. Six-level ladder: cash cost → revenue loss → economic exposure (₹164 Cr) → avoidable (₹107 Cr) → recoverable (₹32 Cr) → net incremental CM (₹18.8 Cr).

### G. Customer segmentation framework
§8. Three separate segmentations for three jobs, plus a fairness overlay with three hard constraints.

### H. Risk framework
§9. 17 features admitted, 5 excluded for leakage. Scorecard-first. Feasibility gate at AUC 0.65.

### I. Intervention framework
§10. Six interventions A–F, prioritised C → F → E → A → B → D.

### J. Experimentation framework
§11–12. Three arms, customer-level randomisation, stratified by risk tier, CM/CSS primary, 30-day maturation, economically derived launch thresholds.

### K. Data requirements for Phase 2

| Table | Grain | Key fields |
|---|---|---|
| `dim_customer` | customer | id, signup_date, tenure, tier/geo, age bucket, historical_orders, historical_rto_rate, historical_cod_share, historical_prepaid_success, payment_failure_count, CLV |
| `dim_product` | product | id, category, price, rating, review_count, return_rate |
| `dim_seller` | seller | id, rating, fulfilment SLA breach rate, tenure |
| `fct_checkout_session` | session | session_id, customer_id, timestamp, `checkout_started`, `address_completed`, `payment_page_reached`, method_shown, method_selected, `payment_attempted`, `payment_success`, `payment_failure_reason`, method_switch_events, `checkout_abandoned`, abandon_step |
| `fct_order` | order | order_id, session_id, customer, product, seller, date, order_value, discount, shipping_fee, cod_fee, payment_method, pincode/tier, `estimated_delivery_days`, `actual_delivery_days` |
| `fct_fulfilment` | order | delivered, cancelled (+actor, +pre/post ship), `rto_flag`, `return_reason`, `delivery_attempts`, delivered_date |
| `fct_order_economics` | order | COGS, fwd_ship, rev_ship, packaging, pg_fee, cod_handling, cod_attempt_fee, reverse_handling, shrink, support, wc_carry, discount_cost, **contribution_margin** |
| `dim_experiment_assignment` | customer × experiment | experiment_id, arm, assigned_at, risk_tier_at_assignment |

**Non-negotiable generation requirements:**
1. **Realistic confounding** — COD selection and RTO must share latent drivers (customer trust propensity, liquidity, intent) so that the naive COD–RTO gap over-states the true payment-method effect. **This is the analytical payoff of the entire project.**
2. **A known ground truth** — record the true generated effect sizes in a held-out `_truth.json` so we can prove the analysis recovers them.
3. **Sufficient noise** — no relationship deterministic; target risk-model AUC ~0.75, not 0.95.
4. **Session-level abandonment** — checkout sessions that never become orders must exist, or the north-star denominator is meaningless.
5. **Prepaid payment failures with method-switch events** — required to test H11.
6. **Time structure** — 90 days of data with weekly seasonality and a month-end liquidity effect, to make the 30-day maturation and RTO censoring problems *real*.

### L. Phase 2 plan — dataset design & generation

| Step | Output |
|---|---|
| 2.1 | Lock `config/params.yaml` — every [A] in §6.4 as a named parameter |
| 2.2 | Write the schema + data dictionary (`docs/01_data_dictionary.md`) before any code |
| 2.3 | Specify the causal DGP explicitly: latent variables (trust propensity, liquidity, intent) → COD selection → RTO, with stated coefficients |
| 2.4 | Generate customers → products/sellers → sessions → orders → fulfilment → economics, in that order |
| 2.5 | Validate against calibration targets: COD 62±1%, blended RTO 16.5±0.5%, CM/order ₹40±3, funnel step rates plausible |
| 2.6 | Write `_truth.json` with the planted effect sizes |
| 2.7 | Load to PostgreSQL; build the analytical views |
| 2.8 | Data-quality report: nulls, ranges, logical consistency (no RTO on cancelled orders, no delivery before order, etc.) |

**Phase 2 gate:** the dataset is accepted only when (a) it hits every calibration target, (b) a naive COD-vs-prepaid RTO comparison measurably over-states the planted effect, and (c) no leakage feature is accidentally correlated with outcomes it shouldn't be.

---

## 15. EXECUTION ROADMAP (5 weeks)

| Week | Phase | Deliverables |
|---|---|---|
| **0 (done)** | 1 — Foundation | This blueprint |
| **1** | 2 — Data | `params.yaml`, data dictionary, generator, 100K dataset, `_truth.json`, PostgreSQL load, DQ report |
| **2** | 3–5 — Diagnosis | Funnel analysis, RTO analysis, hypothesis tests H1–H6 & H11, SQL library (13 queries), ₹165 Cr waterfall reproduced from data |
| **3** | 6–8 — Modelling | Segmentation, scorecard risk model (+ GBM challenger), calibration, fairness audit, per-order CM model, risk-tier P&L |
| **4** | 9–10 — Decision | Intervention simulation (5 scenarios), risk-based pricing decision table, A/B simulation, power analysis, launch criteria |
| **5** | 11–12 — Communication | Power BI (6 pages), PRD, architecture doc, 20-slide case study, resume bullets, interview script |

**Weekly gate:** each phase ends with one written page — *what I found, what decision it enables, what I'd do differently.* If you can't write that page, the phase isn't done.

## 16. FOLDER STRUCTURE

```
checkout-rto-optimization/
├── README.md                      ← the whole story in 400 words + headline chart
├── config/
│   └── params.yaml                ← every assumption, one file
├── docs/
│   ├── 00_phase1_blueprint.md     ← this document
│   ├── 01_data_dictionary.md
│   ├── 02_assumption_registry.md
│   ├── 03_prd_checkout_risk_engine.md
│   ├── 04_technical_architecture.md
│   ├── 05_limitations.md
│   └── 06_interview_script.md
├── data/
│   ├── raw/                       ← generated dataset (gitignored if >100MB)
│   ├── processed/
│   └── truth/_truth.json          ← planted effect sizes
├── notebooks/
│   ├── 01_data_generation.ipynb        08_unit_economics.ipynb
│   ├── 02_data_cleaning.ipynb          09_intervention_simulation.ipynb
│   ├── 03_exploratory_analysis.ipynb   10_ab_test_simulation.ipynb
│   ├── 04_checkout_funnel.ipynb        11_visualizations.ipynb
│   ├── 05_rto_analysis.ipynb           12_final_recommendation.ipynb
│   ├── 06_customer_segmentation.ipynb
│   └── 07_risk_model.ipynb
├── src/
│   ├── generate.py  economics.py  risk.py  interventions.py  experiment.py  viz.py
├── sql/
│   ├── 00_schema.sql  01_views.sql  02_funnel.sql  03_rto.sql
│   ├── 04_segments.sql  05_economics.sql  06_risk.sql  07_experiment.sql
├── dashboards/
│   └── checkout_rto.pbix
└── reports/
    ├── case_study.pdf
    └── figures/
```

## 17. DEFINITION OF DONE

**Analytical**
- [ ] 100K+ orders with documented, non-deterministic causal structure
- [ ] Every metric reproducible from both SQL and Python, with matching values
- [ ] ₹165 Cr waterfall computed from data, with a full sensitivity table
- [ ] Naive vs adjusted COD–RTO effect quantified against `_truth.json`
- [ ] Risk model with time-based validation, calibration curve, and fairness audit
- [ ] Per-order CM model reconciling to the aggregate P&L

**Product**
- [ ] Six interventions with quantified expected impact on all three objectives
- [ ] Risk-based decision table with **economically derived** thresholds
- [ ] PRD with before/after checkout flows
- [ ] Technical architecture with a real-time latency budget

**Experimentation**
- [ ] Three-arm design with pre-registered primary metric and horizon
- [ ] Sample size and duration computed from the actual baseline variance
- [ ] Launch/Iterate/Kill criteria derived from economics
- [ ] Simulated experiment results with correctly interpreted confidence intervals

**Communication**
- [ ] 6-page Power BI dashboard answering one business question per page
- [ ] 20-slide case study with a stated message per slide
- [ ] Three resume bullets, each traceable to a specific artefact
- [ ] Written answers to all 10 hard questions
- [ ] `docs/05_limitations.md` listing at least 8 honest limitations

**The final test:** hand the repo to someone who has never seen it. Within 10 minutes they should be able to state the problem, the core economic insight, the recommendation, and the biggest reason it might be wrong. If they can't, the communication layer isn't done.

---

## 18. OPEN QUESTIONS TO ANSWER BEFORE PHASE 2

1. **Marketplace model** — confirming 1P/managed (platform owns COGS). Locks the CM formula and roughly doubles the opportunity vs. 3P. Agreed?
2. **Sample framing** — confirming 100K = 5% of one month (×240 annualization). This is the only assumption that makes ₹165 Cr arithmetically possible. Are you comfortable stating it openly on the slide?
3. **AOV of ₹1,000** — clean and defensible, but a mixed-category marketplace realistically has a right-skewed AOV distribution (₹1,000 median, ₹1,450 mean). Should Phase 2 generate the skew? *(Recommendation: yes — it makes value-band analysis meaningful and the modelling more interesting.)*
4. **Scope of Intervention D (partial payment)** — model it fully, or model it and descope with a written rationale? *(Recommendation: model it, descope it, and say why — descoping with reasoning is a stronger PM signal than building everything.)*
5. **Include a competitor/market-share dynamic?** *(Recommendation: no. Mention it in limitations. It doubles complexity for marginal narrative gain.)*
