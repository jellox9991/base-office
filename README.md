# BASE Design Office

Private office system for BASE Design (BOQ and quantity takeoff, Kuwait): projects, clients, floor tables,
quotations, drawings, submissions, payments, Archive | الأرشيف, Excel and Revit 2022 handoff.
Arabic and English, KWD with three decimals, works on the office PC and on your phone.

> Private business data. Keep this out of the public `base-design` GitHub Pages repository.

## Open BASE Office (Windows)
1. Install Python 3.11+ from python.org (tick "Add python.exe to PATH").
2. Double-click **`start.bat`**. The first run installs the packages, then the browser opens
   `http://127.0.0.1:5000`.
3. First screen: create your username and password. Then **Settings** opens: check company name **BASE Design**,
   email **basedesignkw@gmail.com**, upload the logo, edit the quotation footer and scope presets.

Phone on the same Wi-Fi: run `start.bat --phone` and open the address it prints. Your data is in `data\`
(database + uploaded files). Back that folder up regularly (OneDrive or USB).

Want to look around first? `python scripts\demo_villa.py demo-data` then
`set BASE_OFFICE_DATA=demo-data` and `python run.py` (sign in demo / demo-pass-123). Never point it at your real data.

## Your first real project
1. **Projects > + New project**. Choose or type the client, type the project name. That is enough: **Save**.
   The number (e.g. BASE-2026-001) is generated.
2. Open the project. **Overview > Edit project details**: owner, area/block/street/plot, building type, basements,
   upper floors, roof, floor height, GF FFL, scope (or a preset such as هيكل أسود + طابوق), due date.
3. **Floors & BOQ**: press **Generate** (e.g. 1 basement + ground + 2 upper + roof). Edit any height, tick **Fixed**
   on levels that must not move, press **Preview recalculation** to see which levels move, then **Save floor table**.
   Tick **Confirmed** once an elevation is checked on the drawings (only confirmed levels go to Revit by default).
4. **New quotation** from the project: lines are prefilled from the scope; set fees, then **Mark as sent** (the PDF is
   frozen) and **Client accepted** (sets the agreed fee). For a job agreed on WhatsApp use *Direct job* in Payments.
5. **Drawings & submissions**: upload client drawings (same title = new revision) and BASE deliverables; record each
   submission (partial or complete). **Payments > Record payment** for deposits and instalments.

## Your first Archive rule
From any project: **Archive** tab > write it in your own words > **Save to Archive**.
* «... اعتمد هالطريقة لكل المشاريع» saves a **company rule** (shown on every applicable BOQ).
* «... لهالمشروع بس» stays on **this project only**.
* "for this client" / «لهالعميل» saves a **client rule** (e.g. their Excel template).
* Unclear wording is saved as a **draft** and is not applied until you approve it.
Rules show on **Floors & BOQ** with the reason they apply, and each recorded submission keeps the exact rule revisions.
**Archive** in the menu: search (Arabic/English), filters, revisions, JSON export/import, and a Markdown summary for Claude.

## See your outstanding payments
* Dashboard: **Who owes me money?** and the **Delivered with balance** filter.
* **Who owes me** in the menu: client, project, delivery date, amount due, WhatsApp button, payment e-mail draft.
* **Clients**: agreed fees, received, outstanding, credits and unallocated money per client.
* **Payments**: cash received per month by the actual payment date.

## E-mails
Each project has **Send quotation**, **Submit BOQ**, **Payment follow-up** drafts (Arabic or English) with the client
e-mail, project number/name and amounts filled in. Step 1 downloads an `.eml` draft with the attachments (opens in
Outlook / Windows Mail for review and sending). Step 2 **I sent it** records it in Activity. Nothing is logged as sent
before that. Optional direct sending from basedesignkw@gmail.com: set `BASE_SMTP_HOST=smtp.gmail.com`,
`BASE_SMTP_USER`, `BASE_SMTP_PASSWORD` (a Gmail app password) before `start.bat`.

## Revit 2022
**Floors & BOQ > Export to Revit 2022** downloads the JSON. Install and verify the importer with
[revit/BaseOfficeImport/README.md](revit/BaseOfficeImport/README.md).

## Tests
```
python -m pytest -q tests                  # full villa workflow through the real web routes
dotnet run --project revit/CoreTests       # Revit importer matching logic (needs .NET SDK)
```
