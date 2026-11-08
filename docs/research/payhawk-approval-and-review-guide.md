# راهنمای جامع و مستند: مدیریت Approval و Return for Review در API و MCP پلتفرم Payhawk

> **تاریخ استخراج و ارزیابی:** ۸ نوامبر ۲۰۲۶ (November 2026)  
> **منابع اصلی مورد ارجاع (Verified Primary Sources):**
> 1. **OpenAPI v3 Specification (Official Machine-Readable Spec):** `https://api.payhawk.com/api/v3/docs.json`
> 2. **Payhawk Developer Portal / Interactive UI:** `https://developers.payhawk.com`
> 3. **Payhawk Developer Documentation:** `https://payhawk.com/help/overview-of-the-payhawk-developer-api`
> 4. **Payhawk Help Center (Official Guides):**
>    - *About the Payhawk MCP:* `https://help.payhawk.com/en/articles/about-payhawk-mcp`
>    - *Connecting Payhawk to your AI Assistant:* `https://help.payhawk.com/en/articles/connecting-payhawk-to-ai-assistant`
>    - *Payhawk MCP Tool Reference:* `https://help.payhawk.com/en/articles/payhawk-mcp-tool-reference`
>    - *Payhawk MCP Playbooks & AI Agents:* `https://help.payhawk.com/en/articles/payhawk-mcp-playbooks-and-ai-agents`
>    - *Reviewing and Approving Expenses:* `https://help.payhawk.com/en/articles/reviewing-and-approving-expenses`

---

## ۱. تفاوت ساختاری دو مفهوم «Approval» و «Review» در اکوسیستم Payhawk

در پلتفرم Payhawk، یک رسید (Receipt) یا فاکتور به عنوان یک فایل ضمیمه (`IFile` / `IExpenseDocument`) به موجودیتی به نام **Expense** (هزینه) تعلق دارد. تمامی عملیات‌های بررسی، تأیید و مرجوع کردن، روی موجودیت Expense اعمال می‌شوند.

در Payhawk دو چرخه کاری (Workflow) کاملاً مجزا و مرحله‌بندی‌شده وجود دارد:

1. **چرخه تأیید مدیریتی (Managerial Approval Workflow):**
   - **هدف:** تأیید هزینه توسط مدیر مستقیم (Team Manager)، مدیر پروژه یا ادمین، پس از ثبت و ارسال هزینه توسط کارمند (`POST .../submit`).
   - **وضعیت‌ها در فیلد `approvalProcess.status`:**
     - `draft` (پیش‌نویس کارمند قبل از ارسال)
     - `pending` (در انتظار تأیید مدیر / Approver)
     - `approved` (تأیید شده)
     - `rejected` (رد شده)
     - `cancelled` (لغو شده)
   - **عملیات بازگرداندن در این مرحله (Return to Employee):** اگر رسید خوانا نباشد یا نیاز به اطلاعات بیشتری باشد، مدیر می‌تواند آن را با یک یادداشت به کارمند بازگرداند (`Return to employee`) تا رسید جدید آپلود کند.

2. **چرخه بازبینی حسابداری (Accounting Review Workflow):**
   - **هدف:** بررسی مدارک مالیاتی، نرخ مالیات (Tax Rate)، کد حسابداری (Account Code) و تطابق فاکتور/رسید با قوانین مالی توسط تیم مالی و حسابدار قبل از اکسپورت به سیستم حسابداری (ERP).
   - **وضعیت‌ها در فیلد `status` موجودیت Expense:**
     - `draft` (در حال بررسی / نیاز به ویرایش حسابداری / مرجوع شده به بازبینی)
     - `ready` (بازبینی شده / Reviewed و آماده ارسال به ERP)
     - `archived` / `deleted`
   - **عملیات بازگرداندن در این مرحله (Return for Review):** اگر هزینه‌ای به عنوان بررسی‌شده ثبت شده باشد اما هنوز تسویه یا قفل نشده باشد، می‌توان آن را برای بازبینی مجدد مرجوع کرد (`Return for review`).

---

## ۲. بررسی از طریق REST API عمومی (Payhawk Developer API v3)

تمام موارد زیر با خواندن مستقیم سند زنده [OpenAPI v3 Spec](https://api.payhawk.com/api/v3/docs.json) ارزیابی و راستی‌آزمایی شده است:

### ۲.۱. نحوه «Return for Review» یا «Mark as Reviewed» از طریق API

برای تغییر وضعیت بازبینی هزینه (Review / Return for review)، اندپوینت اختصاصی زیر در نسخه v3 تعبیه شده است:

* **اندپوینت:**
  ```http
  POST /api/v3/accounts/{accountId}/expenses/{expenseId}/status
  ```
* **توضیحات در سند OpenAPI:**  
  *Summary:* `Review an expense or return for review`  
  *Description:* `Mark an expense as reviewed or return for review`
* **مجوز (Authentication):**
  هدر `X-Payhawk-ApiKey: <API_KEY>` یا `Authorization: Bearer <API_KEY>`
* **ساختار بدنه درخواست (Request Body - `IExpenseStatusPatch`):**
  ```json
  {
    "status": "ready"
  }
  ```
  یا برای مرجوع کردن به بازبینی (Return for review):
  ```json
  {
    "status": "draft"
  }
  ```

> [!NOTE]  
> مقادیر مجاز `enum` در مدل `IExpenseStatusPatch` منحصراً دو مقدار `"draft"` و `"ready"` هستند:
> - ارسال `"ready"` یعنی هزینه بازبینی شد (Reviewed) و به وضعیت Ready منتقل می‌شود.
> - ارسال `"draft"` یعنی هزینه بازبینی نشد / از وضعیت بررسی خارج و به وضعیت Draft مرجوع گردید (Return for review).
> - همین اندپوینت برای گزارش‌های هزینه (Expense Reports) نیز وجود دارد:  
>   `POST /api/v3/accounts/{accountId}/expense-reports/{reportId}/status` با مقادیر `{"status": "ready"}` یا `{"status": "draft"}`.

### ۲.۲. وضعیت عملیات «Approve» در REST API عمومی

* **یافته کلیدی و مستند:**  
  در سند رسمی OpenAPI v3 (`docs.json`)، **هیچ متد یا اندپوینت مستقیمی برای تأیید مدیریتی هزینه (Approve Expense) وجود ندارد**.
* اندپوینت‌های مرتبط با Workflow در REST API عبارتند از:
  1. **ارسال برای تأیید:**
     ```http
     POST /api/v3/accounts/{accountId}/expenses/{expenseId}/submit
     ```
     *Summary:* `Submit an expense for approval`  
     *توضیح:* این متد هزینه را از حالت پیش‌نویس خارج کرده و وارد زنجیره تأیید (Approval Workflow) می‌کند (`approvalProcess.status = "pending"`).
  2. **استعلام وضعیت تأیید و بازبینی:**
     ```http
     GET /api/v3/accounts/{accountId}/expenses/{expenseId}/workflow
     ```
     *Summary:* `Get expense workflow status`  
     *پاسخ دریافتی (`IExpenseWorkflowStatus`):*
     ```json
     {
       "approvalStatus": "pending", // draft | pending | approved | rejected | cancelled
       "approvedAt": "2026-11-08T10:00:00Z",
       "approvedBy": {
         "id": "usr_123",
         "email": "manager@company.com",
         "firstName": "John",
         "lastName": "Doe"
       },
       "reviewedAt": null,
       "reviewedBy": null
     }
     ```
  3. **رویدادهای Webhook:**  
     سیستم Payhawk در زمان تغییرات Workflow، رویدادهای زیر را به وب‌هوک مشترک ارسال می‌کند:
     - `expense.approved` (هنگام تأیید توسط مدیر)
     - `expense.reviewed` (هنگام بازبینی حسابداری)
     - `expense.rejected` (هنگام رد هزینه)

> [!IMPORTANT]  
> چرا در REST API کلید عمومی برای `approve` تعبیه نشده است؟  
> بر اساس معماری امنیتی Payhawk، فرایند Approval نیازمند هویت مشخص کاربر تأییدکننده (Audit Trail فردی) است. API Keyهای سرویسی عمومی نمی‌توانند جایگزین امضای مدیران در زنجیره تأیید شوند. برای اجرای اتوماسیون هوشمند، Payhawk از پروتکل MCP با احراز هویت کاربری (OAuth) بهره می‌برد.

---

## ۳. بررسی از طریق سرور رسمی MCP (Model Context Protocol)

پلتفرم Payhawk دارای یک **سرور رسمی MCP** است که به دستیارهای هوش مصنوعی (مانند Claude Desktop، ChatGPT، Codex، Cursor و ابزارهای سازمانی) اجازه می‌دهد با اختیارات خود کاربر عملیات‌های مالی را اجرا کنند.

* **آدرس سرور MCP رسمی:** `https://mcp.payhawk.com/mcp`
* **پروتکل ارتباطی (Transport):** Streamable HTTP (Remote MCP)
* **شیوه احراز هویت:** پروتکل OAuth 2.0 / OAuth 2.1 با مکانیزم PKCE و Dynamic Client Registration (DCR). این احراز هویت بر اساس حساب کاربری فرد لاگین‌شده انجام می‌شود و مدل دقیقاً همان اختیارات (Permissions) کاربر در پنل Payhawk را به ارث می‌برد.

### ۳.۱. ابزارها و پلی‌بوک اختصاصی «Approval Triage» در MCP

پای‌هاوک در سرور MCP خود دسته‌بندی ابزارهای **Approvals and review** و پلی‌بوک **Approval Triage** را عرضه کرده است:

1. **`needs_my_attention`:**  
   موارد و هزینه‌هایی که در کارتابل شخص منتظر تأیید یا بازبینی هستند را بازیابی و اولویت‌بندی می‌کند.
2. **`get_approval_request`:**  
   جزئیات کامل درخواست هزینه، مبلغ، ردیف‌ها و سند/رسید ضمیمه‌شده (`receipt`) را استخراج می‌کند.
3. **`approve_or_return` (ابزار اصلی برای Approval و مرجوع کردن):**
   - **هدف:** تأیید یک درخواست هزینه در کارتابل کاربر، یا بازگرداندن آن به کارمند (`Return to employee`).
   - **عملکرد:** به دستیار هوش مصنوعی اجازه می‌دهد با دستور متنی، هزینه را Approve کرده یا همراه با دلیل (Comment/Feedback) مرجوع کند (مثلاً: *"رسید ناخواناست یا فاکتور رسمی بارگذاری نشده است"*).
4. **`complete_expense_review`:**  
   اتمام بازبینی مالی هزینه توسط حسابدار (معادل رفتن به وضعیت `ready` در API).
5. **`return_expense_for_review`:**  
   مرجوع کردن هزینه برای بازبینی مجدد (معادل بازگرداندن وضعیت به `draft` در API).

### ۳.۲. گاردریل‌ها و قوانین امنیتی MCP (Safety Guardrails)
- **پیش‌نمایش و تأیید انسانی (Human Confirmation):** تمام ابزارهای نوشتن (Write Tools) نظیر `approve_or_return`، `complete_expense_review` و `return_expense_for_review` موظفند قبل از اجرا، یک پیش‌نمایش (Preview) دقیق از تغییرات را به کاربر نمایش داده و تأییدیه صریح دریافت کنند.
- **تفکیک وظایف (Role Scoping):** فقط کاربرانی که در پنل Payhawk دسترسی Approver یا Reviewer دارند، این ابزارها برای سشن آن‌ها فعال خواهد بود.
- **عدم انتقال مستقیم پول:** انتقال وجه یا پرداخت واقعی از طریق MCP پشتیبانی نمی‌شود و باید در پرتال امن Payhawk انجام گیرد.

---

## ۴. مقایسه و انتخاب راه درست (Decision Matrix)

| هدف و سناریو | راهکار در REST API v3 | راهکار در Payhawk MCP |
| :--- | :--- | :--- |
| **Return for review (بازبینی مجدد حسابداری)** | فراخوانی `POST .../expenses/{id}/status` با مقدار `{"status": "draft"}` | فراخوانی ابزار `return_expense_for_review` |
| **Mark as Reviewed (تأیید بازبینی حسابداری)** | فراخوانی `POST .../expenses/{id}/status` با مقدار `{"status": "ready"}` | فراخوانی ابزار `complete_expense_review` |
| **ارسال هزینه به صف تأیید کارمند** | فراخوانی `POST .../expenses/{id}/submit` | فراخوانی ابزار `submit_expense` |
| **Approve کردن هزینه (تأیید مدیریتی رسید)** | **اندپوینت عمومی ندارد.** (فقط از طریق پرتال وب/موبایل یا Webhook برای شنیدن رویداد `expense.approved`) | فراخوانی ابزار `approve_or_return` با هویت کاربر Approver (OAuth) |
| **بازگرداندن رسید به کارمند (Return to Employee)** | مستقیماً از طریق فیلد وضعیت هزینه در API عمومی ندارد (تنها امکان درج کامنت با `POST .../comments` وجود دارد) | فراخوانی ابزار `approve_or_return` با پارامتر مرجوع و ثبت توضیح |

---

## ۵. نتیجه‌گیری اجرایی (Executive Conclusion)

1. **برای پیاده‌سازی سرویس بک‌اند اتوماتیک (بدون حضور انسان):**
   - برای **Return for review**، مسیر استاندارد و معتبر ارسال درخواست `POST /api/v3/accounts/{accountId}/expenses/{expenseId}/status` با بدنه `{"status": "draft"}` است.
   - برای **Approve**، از آنجا که REST API کلید عمومی به دلایل امنیتی امکان دور زدن چرخه انسانی را فراهم نمی‌کند، تأییدها باید توسط مدیران در پرتال وب، اپلیکیشن، یا از طریق ادغام با سیستم سشن MCP انجام شود و سیستم بک‌اند از طریق وبهوک `expense.approved` ادامه فرآیند را هدایت کند.

2. **برای پیاده‌سازی دستیار هوشمند (AI Assistant / Agentic Workflow):**
   - اتصال مستقیم به سرور رسمی Payhawk MCP به آدرس `https://mcp.payhawk.com/mcp` با استفاده از پروتکل OAuth کاربری.
   - استفاده از ابزار استاندارد `approve_or_return` برای تأیید یا بازگرداندن رسید هزینه، و ابزار `return_expense_for_review` / `complete_expense_review` برای کنترل بازبینی مالی.
