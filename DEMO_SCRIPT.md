# Demo video and interview prep

Say this in your own words. Don't read it. These are the beats to hit and the reasons behind each choice. Narrating *why* is what they're grading.

## Before you record

- Open the deployed link 2 minutes early (the free tier sleeps).
- Runs page, **Reset demo data**. Then go to **Process invoice**.
- Check the header says *Reading with gemini-…*. If you record offline it says *rules parser*. That's fine, but mention it.
- Browser zoom at about 110%, close other tabs, notifications off.
- Do one full rehearsal, then **reset again** (rehearsal runs change the ledger, so a second run of 01 becomes a duplicate).

## The 5 minutes

Before recording: run **12**, approve it with a note ("Freight surcharge agreed with procurement for Q4"), run **13**, approve it too. That way the rule suggestion is already waiting on the Runs page and you don't spend video time on setup.

**0:00–0:30 · The problem, and how you scoped it**
- "Exceptions are the #1 AP problem for 53% of AP leaders (Ardent Partners), and vendor impersonation is up to 45% of companies (AFP). So I built around exceptions and fraud, not just data entry."
- "My one design rule: the AI reads the invoice, code makes the decision. Every outcome traces to a named check."

**0:30–1:20 · Happy path: run 01**
- Narrate the stages as they tick: fingerprint, text layer, LLM read plus independent parser cross-check, arithmetic and GSTIN check digit, vendor, duplicates, PO line by line.
- Stamp: approved. Point at "paid to the verified account on file, not the one printed on the invoice".

**1:20–2:05 · Edge case: run 03, split-PO over-billing**
- "Valid on its own. Any one-invoice-at-a-time check approves it." Show the PO bar: ₹4L of ₹6L already billed, this adds ₹4L.
- Show the payment-due line: "it also tells the reviewer how long they have before this becomes a late payment."

**2:05–2:45 · Edge case: run 07, bank change**
- "Everything matches except the bank account, which is the classic impersonation pattern." Point out that it drafts **no** email to the invoice's contact details.

**2:45–3:40 · A real document: upload your Myntra PDF**
- "I tested on my own Myntra and Zomato invoices, because synthetic data is too clean."
- It splits into 3: "one download, three sellers, and page 3 is a duplicate copy it dropped."
- Open a part: **NOT AP**. "It's billed to me as an unregistered customer, so it isn't a supplier invoice. It goes to expense claims, not payment."
- One line: "Real invoices also caught a bug: my GST rule assumed our state. It now reads the place of supply."

**3:40–4:35 · Learning from reviewers**
- Runs page: "Reviewers approved two Sharma price holds. Same 3.5% surcharge both times. The system suggests a 4% tolerance for Sharma only."
- Click **Apply**, then run **14**: approved, with "Sharma's agreed tolerance is 4% (learned from 2 reviewer approvals)".
- "Exceptions only drop if the system learns. But fraud and duplicate holds are never relaxed, however often they're approved."

**4:35–5:00 · Limits and next**
- Two-way match only, one entity; next is inbox ingestion and goods receipts.
- On the touchless rate: "the test set is deliberately exception-heavy."

**After recording:** Reset demo data. The Render link is public and your invoice has your name and address on it.

## Live interview: run order

1. 01 happy path
2. 03 over-billing (cross-invoice reasoning)
3. 07 bank change (fraud judgment)
4. 05 scan (OCR, repair, inferred PO)
5. Re-upload 01, which is rejected as a duplicate
6. If they want more: 04 disguised duplicate, 10 wrong GST type
7. Upload a real marketplace PDF (split + routed to expenses)
8. Learning loop: approve 12 and 13, apply the suggestion, run 14
9. Dashboard, then approve one held invoice live

Upload one of *their* invoices if they offer it. With the LLM on, it should read it; it'll likely be held for an unknown vendor or PO, and that's exactly what should happen.

## Questions you'll probably get

**Why not let the LLM make the decision?**
Money decisions have to be explainable, repeatable and auditable. An LLM might approve the same invoice on Monday and hold it on Friday. It's excellent at reading messy documents, so that's the only job it gets.

**What if the LLM misreads a number?**
A regex parser reads the same document independently. If they disagree on any money field, the invoice is held. Arithmetic checks (lines = subtotal, subtotal + tax = total) catch most misreads too. I tested this by faking a misread total: it was caught by both.

**Why are some outcomes "hold" and others "reject"?**
Reject means it can't be booked as-is and the vendor has to fix it (proforma, wrong GSTIN, wrong tax head). Hold means it might be fine but a person should look before money moves (variances, duplicates, bank changes). The difference is who has to act.

**How did you pick the tolerances?**
2% on price and cumulative value is a common AP default. They're config, shown in the UI, and in production they'd be set per vendor or category. Overrides from reviewers are the data you'd use to tune them.

**How does duplicate detection avoid false positives on monthly bills?**
Retainers legitimately share amount and PO every month. So amount plus PO only counts as a duplicate signal when the dates are within 10 days. A matching invoice number after normalising formatting (`BSE/2526/0042` vs `BSE-2526-42`) counts on its own.

**How do you infer a PO when none is quoted?**
Score each open PO for that vendor on line-description similarity (75%) and how well the amount fits what's left on the PO (25%). It only suggests one if it clears 60% and beats the next best by 10 points, and it's always held for a person to confirm.

**What's the GSTIN repair?**
OCR confuses O/0 and I/1. A GSTIN has a fixed shape (2 digits, 5 letters, 4 digits…) and a mod-36 check digit, so the fix is deterministic, not a guess. The field is still marked lower confidence.

**What about a phone photo of an invoice?**
Photos are converted to PDF and OCR'd, and small images are upscaled first. Two OCR passes in different layout modes fill each other's gaps. If a mandatory field still can't be read, it's held for a person to key in, not rejected: on a scan, "couldn't read it" isn't the same as "the vendor left it out". (Try it: screenshot invoice 11 and upload the PNG.)

**Could the GSTIN repair make a fake GSTIN look valid?**
No. It only runs on OCR text, and it only changes characters that break the GSTIN's shape (a letter where a digit must be) plus a lookalike swap on the check character. A GSTIN printed in a real text layer is never altered, which is why QuickFix's bad check digit is still caught.

**Why not auto-apply what it learns?**
A tolerance change is a control change, so a person approves it. The suggestion shows the evidence: which runs, the variances, the reviewer notes. It's scoped to one vendor, and the reason is stored with the rule, so an auditor can see why Sharma gets 4%.

**What stops it learning bad habits?**
Fraud and duplicate holds (bank change, lookalike vendor, duplicates, unknown vendor) are excluded from learning entirely. It also needs at least 2 cases and 75% approval before suggesting anything, and rejections count against it.

**Why route personal bills to expenses instead of rejecting them?**
Rejecting would email Myntra asking them to fix an invoice that's correct for what it is. The right owner is the employee's expense claim. Sending it to the wrong queue is the actual problem.

**How does the split work?**
Each page's document number (invoice number or bill of supply number) is read. A new number starts a new document, a page without one continues the previous one, and an exact repeat of a page is dropped. Each part becomes its own run, linked to the original upload.

**Where did your pain points come from?**
Ardent Partners' 2025 AP benchmarks (212 AP leaders), the AFP 2025 payments fraud survey, APQC benchmarks on duplicates, and my own invoices for the document problems. They're on the *How it decides* page with links.

**How would this scale?**
Swap SQLite for Postgres, run stages from a job queue, ingest from an AP inbox, and add the ERP posting API. The stage and finding structure stays the same.

**What would you do with another week?**
Three-way match with goods receipts, an inbox connector so invoices arrive without anyone uploading them, and learning extended to quantity and PO-value tolerances.

**What would you measure in production?**
Touchless rate, exceptions by reason, how often reviewers override each rule (a rule that's always overridden is miscalibrated), time from receipt to decision, and the value of duplicates and fraud stopped.
