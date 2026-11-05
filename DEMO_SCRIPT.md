# Demo video and interview prep

Say this in your own words. Don't read it. These are the beats to hit and the reasons behind each choice. Narrating *why* is what they're grading.

## Before you record

- Open the deployed link 2 minutes early (the free tier sleeps).
- Runs page, **Reset demo data**. Then go to **Process invoice**.
- Check the header says *Reading with gemini-2.5-flash*. If you record offline it says *rules parser*. That's fine, but mention it.
- Browser zoom at about 110%, close other tabs, notifications off.
- Do one full rehearsal, then **reset again** (rehearsal runs change the ledger, so a second run of 01 becomes a duplicate).

## The 5 minutes

**0:00–0:30 · The problem and the one idea**
- An AP clerk opens each invoice, finds the PO, checks the numbers and decides. It's slow, and the expensive mistakes are the subtle ones.
- "My one design rule: the AI reads the invoice, but code makes the decision. Every outcome traces to a named check, and the same invoice always gets the same answer."

**0:30–1:45 · Happy path: run 01**
- Click it and let the stages tick through. Narrate as they go:
  - Receive: it fingerprints the file, which catches the exact same file being resent.
  - Read: it has a text layer. A scan would go to OCR.
  - Extract: the LLM reads it into a fixed schema with confidence per field, and a separate parser reads it too. Both reads are compared.
  - Validate: arithmetic, GSTIN check digit, and GST type. Chennai to Gurugram is inter-state, so IGST is correct.
  - Match the vendor, check duplicates against the ledger, match the PO line by line.
- Stamp lands: approved. Show the posting record (due date from the vendor's Net 30 terms, paid to the verified account on file, not the one printed on the invoice).

**1:45–2:45 · Edge case 1: run 03, split-PO over-billing**
- "This invoice is perfectly valid on its own. Any check that looks at one invoice at a time approves it."
- Open *Match purchase order* and show the bar: July and August already used ₹4L of a ₹6L PO, and this one adds ₹4L.
- It's held for Procurement, and a vendor query is drafted to go out after review.

**2:45–3:45 · Edge case 2: run 07 (bank change) or 06 (price creep)**
- 07: "Everything matches except the bank account. This is the most common invoice-fraud pattern." Point at the warning: it tells the reviewer to call the number on file, and it deliberately drafts **no** email to the address on the invoice.
- 06 (if you prefer): the total is within 0.3% of the PO, but there are 5 fewer chairs at 14% more each. "A check on totals alone would have paid this."

**3:45–4:30 · Dashboard and audit trail**
- Run all test invoices (start it before this segment, or have it already filled).
- Show the touchless rate, the review queue, the *Why invoices stopped* chart and the value stopped before payment.
- Open one held run and approve it with a note. Show it's now in the ledger with the reviewer's note: that's the audit trail.

**4:30–5:00 · Honest limits and what's next**
- Limits: two-way match only (no goods receipts), one entity, INR.
- Next: email-inbox ingestion, three-way match with GRNs, and learning from reviewer overrides to tune tolerances per vendor.

If you have spare seconds, the scan (05) is the best "wow": it shows `OCR fixed`, the raw OCR read `O6AAH…`, and the PO inferred from the line items with the ranking of the alternatives.

## Live interview: run order

1. 01 happy path
2. 03 over-billing (cross-invoice reasoning)
3. 07 bank change (fraud judgment)
4. 05 scan (OCR, repair, inferred PO)
5. Re-upload 01, which is rejected as a duplicate
6. If they want more: 04 disguised duplicate, 10 wrong GST type
7. Dashboard, then approve one held invoice live

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

**How would this scale?**
Swap SQLite for Postgres, run stages from a job queue, ingest from an AP inbox, and add the ERP posting API. The stage and finding structure stays the same.

**What would you do with another week?**
Three-way match with goods receipts, per-vendor tolerances learned from overrides, and an inbox connector so invoices arrive without anyone uploading them.

**What would you measure in production?**
Touchless rate, exceptions by reason, how often reviewers override each rule (a rule that's always overridden is miscalibrated), time from receipt to decision, and the value of duplicates and fraud stopped.
