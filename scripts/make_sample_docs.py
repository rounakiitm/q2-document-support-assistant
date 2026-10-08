"""Generate the sample knowledge base: 8 insurance-domain docs in mixed formats.

MD files are written directly; DOCX via python-docx; PDF via fpdf2.
Run:  python scripts/make_sample_docs.py
"""

from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed_docs"

# --------------------------------------------------------------------- MD

POLICY_WORDING_MD = """# Apex Term Assurance — Policy Wording

## 1. Definitions
**Sum Assured** means the amount stated in the Schedule payable on the
occurrence of the Insured Event. **Policy Anniversary** means the anniversary
of the Commencement Date. **Free Look Period** means 15 days (30 days for
e-commerce channels) from receipt of the Policy, during which the Policyholder
may review the terms and return the Policy.

## 2. Cover
The Company shall pay the Sum Assured to the Nominee upon the death of the
Life Assured during the Policy Term, provided the Policy is In Force.
Death benefit is paid within 30 days of receipt of all claim documents.

## 3. Exclusions
No benefit is payable if death occurs within the first Policy Year due to
suicide, self-inflicted injury, or participation in criminal acts. In such
cases, 80% of premiums paid are refunded to the Nominee.

## 4. Grace Period and Revival
A Grace Period of 30 days applies to annual, half-yearly and quarterly modes,
and 15 days to monthly mode. A lapsed Policy may be revived within two
consecutive Policy Years from the date of first unpaid premium, subject to
evidence of insurable health and payment of arrears with interest at 9% p.a.

## 5. Free Look Cancellation
On cancellation within the Free Look Period, the Company refunds the premium
less proportionate risk premium for the period of cover, medical expenses and
stamp duty.
"""

CLAIMS_MD = """# Claims Procedure Guide

## Intimation
A claim must be intimated within 90 days of the Insured Event through the
portal, the call centre, or any branch office. Intimation requires the Policy
Number, the date of the event, and the claimant's contact details.

## Documents Required
The following must reach the Company within 30 days of intimation: (a) duly
completed claim form; (b) original Policy document; (c) death certificate
issued by the municipal authority; (d) photo identity of the claimant;
(e) cancelled cheque of the claimant's bank account. For claims above
Rs 5,00,000 or within three Policy Years of commencement, additional
documents may be called for, including medical records and an investigation.

## Decision Timelines
Non-investigated claims are settled within 15 working days of complete
documents. Investigated claims are settled within 30 working days. Interest
at 2% above bank rate applies to delays beyond 30 days.

## Repudiation and Appeal
If a claim is repudiated, the claimant may approach the Grievance Redressal
cell within 30 days, then the Insurance Ombudsman, as described in the
Grievance Redressal document.
"""

NOMINEE_MD = """# Nomination and Beneficiary Rules

## Who Can Be a Nominee
Any person with insurable interest may be nominated: spouse, children,
parents, siblings, or any other person the Policyholder chooses. A minor
nominee requires an appointee who is the Policyholder or their family member.

## Beneficial Nomination
Under Section 39 of the Insurance Act, 1938, a nomination in favour of
spouse, children or parents (jointly or individually) is a beneficial
nomination: such nominees receive the death benefit for their own right and
not on behalf of heirs.

## Changing a Nomination
A nomination may be changed any number of times during the Policy Term by
written request, without the consent of the previous nominee. Changes take
effect from the date the Company records them.
"""

FATCA_MD = """# FATCA Declaration — Explainer

## Purpose
FATCA (Foreign Account Tax Compliance Act) requires insurers to identify
Policyholders with US tax residence or reportable indicia. A self-certification
is mandatory for proposals with annualised premium above USD 50,000
equivalent.

## Indicia That Trigger Review
US place of birth, US postal address, US telephone number, standing
instructions to transfer funds to a US account, or a US TIN.

## What the Policyholder Must Provide
Full name, permanent address, country of tax residence, TIN or explanation
if none exists, and the signed declaration. Indicia do not automatically make
a person reportable; the self-certification decides.
"""

# ------------------------------------------------------------- DOCX content

GRIEVANCE_DOCX = [
    ("Heading 1", "Grievance Redressal Procedure"),
    ("Heading 2", "What Counts as a Grievance"),
    ("Normal", "A grievance is any expression of dissatisfaction about the Company's products, services, or the actions of its staff or agents, other than a formal claim dispute."),
    ("Heading 2", "Escalation Levels"),
    ("Normal", "Level 1: contact the servicing branch or the call centre. Acknowledgement within 3 working days, resolution within 15 working days."),
    ("Normal", "Level 2: write to the Nodal Officer at the head office with the complaint reference. Resolution within 15 working days of the written complaint."),
    ("Normal", "Level 3: approach the Insurance Ombudsman if the grievance is unresolved for 30 days or is rejected. The Ombudsman's award binds the Company up to Rs 50,00,000."),
    ("Heading 2", "Contact Points"),
    ("Normal", "Toll-free number 1800-000-000 operates on business days from 9:00 to 18:00. Email grievances@apexlife.example and the postal address is printed on every Policy document."),
]

NACH_DOCX = [
    ("Heading 1", "NACH / ECS Mandate — How-To"),
    ("Heading 2", "What the Mandate Does"),
    ("Normal", "A NACH (National Automated Clearing House) mandate authorises your bank to debit premium instalments automatically on the due date. It replaces manual renewal payment."),
    ("Heading 2", "How to Register"),
    ("Normal", "Fill the NACH mandate form with: bank account number, IFSC code of the branch, bank name, the debit amount in figures, and the frequency (monthly, quarterly, half-yearly or annual). Sign it exactly as the name appears in the bank record."),
    ("Normal", "Submit the signed mandate at any branch or upload it on the portal. Registration is confirmed within 7 working days by SMS and email."),
    ("Heading 2", "Amendment and Cancellation"),
    ("Normal", "To change the debit amount, frequency, or bank account, submit a fresh mandate. The old mandate stays live until the new one is confirmed. Cancellation requires 15 days' notice before the next due date."),
]

SUITABILITY_PDF_TITLE = "Suitability Profiler Declaration — Notes"
SUITABILITY_PDF_SECTIONS = [
    ("Purpose", "The Suitability Profiler records whether the recommended product matches the customer's income stability, financial goals and risk appetite. It must be completed before the proposal is signed."),
    ("Who Completes It", "The Agent or SP records the customer's responses; the customer signs the declaration. Fields include the Application Number, Name of Life Assured, and the Agent/SP code with the place and date of the discussion."),
    ("Red Flags", "A recommendation is unsuitable if the premium exceeds 30% of stated annual income, if the customer's stated horizon is shorter than the Policy Term, or if liquidity needs conflict with surrender charges."),
]

MHQ_PDF_TITLE = "Moral Hazard Questionnaire — Field Guide"
MHQ_PDF_SECTIONS = [
    ("When It Applies", "The Moral Hazard Questionnaire (MHQ) is mandatory for proposals where the sum assured exceeds 25 times annual income, for applicants above age 55, or where medical evidence shows impairment."),
    ("Fields", "The MHQ captures the Application Number, Name of Life Assured, nominee relationship to the proposer, and the place and date where the questionnaire was completed."),
    ("Assessment", "Underwriting reviews occupation, avocation, income consistency and prior declines. Inconsistent nominee relationships or missing places/dates are the most common reasons an MHQ is returned for rework."),
]

# ------------------------------------------------------------------ writers

def write_md(name: str, text: str) -> None:
    (SEED_DIR / name).write_text(text, encoding="utf-8")


def write_docx(name: str, blocks: list[tuple[str, str]]) -> None:
    from docx import Document

    doc = Document()
    for style, text in blocks:
        doc.add_paragraph(text, style=style)
    doc.save(SEED_DIR / name)


def write_pdf(name: str, title: str, sections: list[tuple[str, str]]) -> None:
    from fpdf import FPDF

    def ascii_safe(text: str) -> str:
        return (
            text.replace("\u2014", "-").replace("\u2013", "-")
            .replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
        )

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", "B", 16)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 8, ascii_safe(title))
    pdf.ln(2)
    for heading, body in sections:
        pdf.set_font("helvetica", "B", 12)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, ascii_safe(heading))
        pdf.set_font("helvetica", "", 10)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, ascii_safe(body))
        pdf.ln(2)
    pdf.output(SEED_DIR / name)


def main() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    write_md("policy_wording.md", POLICY_WORDING_MD)
    write_md("claims_procedure.md", CLAIMS_MD)
    write_md("nominee_rules.md", NOMINEE_MD)
    write_md("fatca_explainer.md", FATCA_MD)
    write_docx("grievance_redressal.docx", GRIEVANCE_DOCX)
    write_docx("nach_mandate_howto.docx", NACH_DOCX)
    write_pdf("suitability_profiler.pdf", SUITABILITY_PDF_TITLE, SUITABILITY_PDF_SECTIONS)
    write_pdf("moral_hazard_questionnaire.pdf", MHQ_PDF_TITLE, MHQ_PDF_SECTIONS)
    print(f"8 seed documents written to {SEED_DIR}")


if __name__ == "__main__":
    main()