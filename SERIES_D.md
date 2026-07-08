# Series D — the structurally-absent fourth series (verification scaffold)

> **Status: SCAFFOLD, not a completed verification.** This file states the claim,
> lays out *how* to check it, and records known counter-evidence. It contains
> **zero asserted verification results** — the evidence log (§4) is `TODO`. The
> maintainer performs the verification and fills it in. Created 2026-07-08.

The repo runs a four-cell directional grid:

| cell | direction | instrument | status |
|---|---|---|---|
| A — granica | PL→UA physical wait | granica.gov.pl SOAP | live |
| B — eCherga | UA→PL virtual queue | back.echerha.gov.ua | live |
| C — DPSU | UA→PL physical count | dpsu.gov.ua/uk/map | live |
| **D — PL→UA virtual** | **PL→UA virtual queue** | **— (none found)** | **claimed structurally absent** |

Cells A–C are logged. Cell D — a Polish-operated booking / virtual-queue
instrument for trucks heading **PL→UA** — is claimed not to exist. That claim is
load-bearing (see §2), so it deserves a real verification protocol rather than a
parenthetical.

---

## 1. Claim (dated, precisely scoped)

**As of 2026-07-08: there is no Polish-operated booking or virtual-queue
instrument for PL→UA road-freight (trucks) at any of the nine corridor crossings**
(Dorohusk, Zosin, Dołhobyczów, Hrebenne, Budomierz, Korczowa, Medyka, Małhowice,
Krościenko). Poland publishes *wait times* (granica, Series A) but does **not**
meter outbound trucks through a bookable electronic queue the way Ukraine's
eCherga meters the UA→PL direction (Series B).

Scope boundaries (what the claim does *not* say):
- It is **not** a claim about Ukraine's e-queue (that is Series B, and it exists).
- It is **not** a claim about the PL–BY border (see the eBooking TRUCK
  counter-example, §5).
- It is **not** a claim about passenger cars or buses — freight (trucks) only.

---

## 2. Why it matters

The article's headline directional asymmetry — that the UA→PL exit direction is
instrumented **twice** (physical C + virtual B) while the PL→UA direction is
instrumented **once** (physical A only) — rests entirely on D being absent. If a
Polish PL→UA truck-booking instrument exists and we simply missed it, the
"observed twice vs once" framing collapses. So this is the single external claim
whose falsification would most damage the thesis; it must be verified, not
assumed, and re-verified over time (policy here is volatile — §5).

---

## 3. Verification protocol — surfaces to check and how

| surface | what to check | how |
|---|---|---|
| **granica.gov.pl** | any booking / "rezerwacja" / e-queue feature for outbound trucks, beyond the wait-time widget | UI walk-through + inspect the SOAP surface (the same service Series A polls) for any booking operation |
| **Straż Graniczna / MSWiA** | komunikaty announcing a PL-side truck booking system | strazgraniczna.pl + gov.pl/web/mswia announcements search |
| **KAS / PUESC** | confirm **eBooking TRUCK remains scoped to the PL–BY corridor only** (Koroszczyn/Kukuryki) and has **not** been extended to any PL–UA crossing — this is the counter-example a reviewer raises first (§5) | puesc.gov.pl / KAS announcements; check the eBooking TRUCK crossing list |
| **Ministry of Infrastructure** | any announced PL→UA freight-queue pilot | gov.pl/web/infrastruktura announcements |

Method note: absence is proven by *checking the surfaces where it would appear
and finding nothing*, not by not-having-seen-it. Record each surface checked in
§4 even when the result is "nothing found" — a dated nil check is the evidence.

---

## 4. Evidence log

*(Maintainer fills this in. One row per surface actually checked. A nil result is
a valid, required row — it is what substantiates the claim.)*

| date | surface | method | result | checked_by |
|---|---|---|---|---|
| `TODO(maintainer)` | | | | |

---

## 5. Known counter-evidence and policy volatility

Two facts a reviewer will (rightly) raise:

1. **The PL–BY eBooking TRUCK counterexample.** Poland *does* operate an
   electronic truck-booking system — **eBooking TRUCK** — but on the **PL–Belarus**
   corridor (Koroszczyn/Kukuryki), not PL–UA. It is the reason the claim is scoped
   to "at any of the nine *corridor* crossings": the instrument exists in Poland,
   just not on this border. Verification must confirm it has **not** quietly
   extended to a PL–UA crossing.

2. **Policy volatility — the e-queue itself has been contested here.** In 2024
   Poland's infrastructure ministry **requested abolition of the Ukrainian e-queue**
   at three crossings, with **pilot abolitions at Nyzhankovychi–Małhowice and
   Uhryniv–Dołhobyczów**.
   - L1 (verified 2026-07-08, HTTP 200): <https://interfax.com/newsroom/top-stories/99611/>
     — "Poland asks Ukraine to abolish e-Queue system at 3 border crossings" (2024).
   - L2 (verified 2026-07-08, HTTP 200): <https://english.nv.ua/nation/poland-requests-e-queue-cancellation-at-three-border-checkpoints-50394283.html>
     — same episode, second source (2024).

   Analytical significance: (a) the queue configuration on this corridor is
   **policy-volatile**, so D's absence must be **re-verified on a cadence** (§7),
   not settled once; and (b) Poland actively lobbying to *remove* a Ukrainian
   e-queue supports a **deliberate-policy-stance** reading of Polish non-adoption
   of its own PL→UA queue — a reading the article should engage, not treat as a
   mere gap.

---

## 6. Falsification conditions

The claim is **false** (and this file must be updated, D promoted toward "live"
or "exists but unlogged") if **any** of the following is found:

- any Polish-operated booking / virtual-queue instrument for PL→UA **trucks** at
  **any** of the nine corridor crossings;
- any announced **pilot** of such an instrument at a corridor crossing;
- eBooking TRUCK (or a successor) extended from PL–BY to any PL–UA crossing.

---

## 7. Re-verification cadence

**Monthly.** Because the underlying policy is volatile (§5), a one-time check is
insufficient. Each monthly re-check is logged as a row in the D2 event-log survey
file (`data/corridor_events.surveys.csv`) — record the surfaces checked and the
result there, then reflect any material finding back into §4 and §1 here.

---

*Cross-references:* series grid — `analysis/METHODOLOGY.md` (series overview) and
`RECON_event_log.md` §0/§1 (where D was first noted as structurally absent);
survey cadence — `data/corridor_events.README.md` (survey protocol, D2).
