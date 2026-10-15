
/* ==========================================
   CREDORA SHARED JAVASCRIPT
========================================== */

document.addEventListener("DOMContentLoaded", () => {
  // Navbar
  const menuButton = document.querySelector(".menu-toggle");
  const nav = document.querySelector(".nav-links");

  if (menuButton && nav) {
    menuButton.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      menuButton.setAttribute("aria-expanded", String(open));
      menuButton.textContent = open ? "✕" : "☰";
    });

    nav.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        nav.classList.remove("open");
        menuButton.setAttribute("aria-expanded", "false");
        menuButton.textContent = "☰";
      });
    });
  }

  // Highlight current page
  const currentPage = location.pathname.split("/").pop() || "index.html";

  document.querySelectorAll(".nav-links a").forEach((link) => {
    if (link.getAttribute("href") === currentPage) {
      link.classList.add("active");
      link.setAttribute("aria-current", "page");
    }
  });

  // Footer year
  document.querySelectorAll("[data-year]").forEach((element) => {
    element.textContent = new Date().getFullYear();
  });

  // Page-specific record manager
  document.querySelectorAll("[data-record-app]").forEach(initRecordApp);

  // Document upload preview
  const fileInput = document.querySelector("#documentFile");
  const preview = document.querySelector("#documentPreview");

  if (fileInput && preview) {
    fileInput.addEventListener("change", () => {
      preview.replaceChildren();

      const file = fileInput.files?.[0];
      if (!file) return;

      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = `Selected file: ${file.name} (${formatBytes(file.size)})`;
      preview.append(note);

      const url = URL.createObjectURL(file);

      if (file.type.startsWith("image/")) {
        const img = document.createElement("img");
        img.src = url;
        img.alt = "Selected document preview";
        img.style.maxHeight = "420px";
        img.style.objectFit = "contain";
        preview.append(img);
      } else if (file.type === "application/pdf") {
        const frame = document.createElement("iframe");
        frame.src = url;
        frame.title = "PDF document preview";
        frame.style.width = "100%";
        frame.style.height = "500px";
        preview.append(frame);
      } else if (
        file.type.startsWith("text/") ||
        /\.(txt|csv)$/i.test(file.name)
      ) {
        const pre = document.createElement("pre");
        pre.style.whiteSpace = "pre-wrap";
        pre.style.overflowWrap = "anywhere";
        preview.append(pre);

        file.text().then((content) => {
          pre.textContent = content.slice(0, 30000);
        });
      } else {
        const message = document.createElement("p");
        message.textContent = "Preview is not available for this file type.";
        preview.append(message);
      }
    });
  }

  // Demo contact form
  const contactForm = document.querySelector("[data-contact-form]");
  const contactStatus = document.querySelector("[data-contact-status]");

  if (contactForm && contactStatus) {
    contactForm.addEventListener("submit", (event) => {
      event.preventDefault();

      if (!contactForm.reportValidity()) return;

      contactStatus.textContent =
        "Your form is complete, but this demo does not send messages. Connect a backend or email service to enable submissions.";
      contactStatus.classList.add("visible");
      contactForm.reset();
    });
  }
});

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function initRecordApp(root) {
  const key = root.dataset.recordApp;
  const form = root.querySelector("[data-record-form]");
  const tbody = root.querySelector("[data-records]");
  const search = root.querySelector("[data-search]");
  const status = root.querySelector("[data-record-status]");
  const exportButton = root.querySelector("[data-export]");
  const importInput = root.querySelector("[data-import]");

  if (!key || !tbody) return;

  const columns = [...root.querySelectorAll("[data-column]")].map((el) => ({
    key: el.dataset.column,
    label: el.textContent.trim()
  }));

  let records = readRecords(key);

  function showMessage(message) {
    if (!status) return;
    status.textContent = message;
    status.classList.add("visible");
  }

  function render() {
    const term = (search?.value || "").trim().toLowerCase();
    const filtered = records.filter((record) =>
      columns.some((column) =>
        String(record[column.key] ?? "").toLowerCase().includes(term)
      )
    );

    tbody.replaceChildren();

    if (!filtered.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = columns.length + 1;
      td.className = "empty-state";
      td.textContent = records.length
        ? "No matching records found."
        : "No records yet. Add or import your first record.";
      tr.append(td);
      tbody.append(tr);
    } else {
      filtered.forEach((record) => {
        const tr = document.createElement("tr");

        columns.forEach((column) => {
          const td = document.createElement("td");
          td.textContent = record[column.key] ?? "—";
          tr.append(td);
        });

        const actionCell = document.createElement("td");
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "btn btn-outline btn-small";
        deleteButton.textContent = "Delete";

        deleteButton.addEventListener("click", () => {
          records = records.filter((item) => item.id !== record.id);
          saveRecords(key, records);
          render();
          showMessage("Record deleted from this browser.");
        });

        actionCell.append(deleteButton);
        tr.append(actionCell);
        tbody.append(tr);
      });
    }

    const count = root.querySelector("[data-count]");
    if (count) count.textContent = String(records.length);
  }

  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();

      if (!form.reportValidity()) return;

      const record = { id: crypto.randomUUID() };

      form.querySelectorAll("[name]").forEach((field) => {
        if (field.type === "file") return;
        record[field.name] = field.value.trim();
      });

      records.unshift(record);
      saveRecords(key, records);
      form.reset();
      render();
      showMessage("Record added to this browser.");
    });
  }

  if (search) search.addEventListener("input", render);

  if (exportButton) {
    exportButton.addEventListener("click", () => {
      const exportRows = [
        columns.map((column) => column.label),
        ...records.map((record) =>
          columns.map((column) => record[column.key] ?? "")
        )
      ];

      const csv = exportRows
        .map((row) =>
          row.map((value) =>
            `"${String(value).replace(/"/g, '""')}"`
          ).join(",")
        )
        .join("\r\n");

      downloadFile(`${key}.csv`, csv, "text/csv;charset=utf-8");
    });
  }

  if (importInput) {
    importInput.addEventListener("change", async () => {
      const file = importInput.files?.[0];
      if (!file) return;

      try {
        const text = await file.text();
        const rows = parseCSV(text);

        if (rows.length < 2) {
          showMessage("The CSV contains no records to import.");
          return;
        }

        const headers = rows[0].map((value) => value.trim().toLowerCase());
        let imported = 0;

        rows.slice(1).forEach((row) => {
          if (!row.some((value) => value.trim())) return;

          const record = { id: crypto.randomUUID() };

          columns.forEach((column) => {
            const index = headers.indexOf(column.label.toLowerCase());
            record[column.key] = index >= 0 ? (row[index] || "").trim() : "";
          });

          records.push(record);
          imported++;
        });

        saveRecords(key, records);
        render();
        showMessage(`${imported} record(s) imported.`);
      } catch {
        showMessage("Unable to import this CSV file.");
      }

      importInput.value = "";
    });
  }

  render();
}

function readRecords(key) {
  try {
    const data = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function saveRecords(key, records) {
  try {
    localStorage.setItem(key, JSON.stringify(records));
  } catch {
    alert("Browser storage is full or unavailable. Export your data before continuing.");
  }
}

function downloadFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = url;
  link.download = filename;
  link.click();

  URL.revokeObjectURL(url);
}

function parseCSV(text) {
  const rows = [];
  let row = [];
  let value = "";
  let quoted = false;

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const next = text[i + 1];

    if (char === '"' && quoted && next === '"') {
      value += '"';
      i++;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(value);
      value = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") i++;
      row.push(value);
      rows.push(row);
      row = [];
      value = "";
    } else {
      value += char;
    }
  }

  if (value.length || row.length) {
    row.push(value);
    rows.push(row);
  }

  return rows;
}

const navLogin = document.getElementById("loginLink");
const profileContainer = document.getElementById("profileContainer");
const profileButton = document.getElementById("profileButton");
const profileDropdown = document.getElementById("profileDropdown");
const profileName = document.getElementById("profileName");
const profileEmail = document.getElementById("profileEmail");
const profileImage = document.getElementById("profileImage");
const logoutButton = document.getElementById("logoutButton");

function updateNavbar() {
  let user = null;

  try {
    user = JSON.parse(localStorage.getItem("credoraUser") || "null");
  } catch {
    user = null;
  }

  const loggedIn = Boolean(user && user.email);

  if (navLogin) navLogin.hidden = loggedIn;
  if (profileContainer) profileContainer.hidden = !loggedIn;

  if (loggedIn) {
    if (profileName) profileName.textContent = user.name || "CREDORA User";
    if (profileEmail) profileEmail.textContent = user.email;

    if (profileImage) {
      profileImage.src = user.image || "profile.png";
      profileImage.onerror = () => {
        profileImage.onerror = null;
        profileImage.src =
          "data:image/svg+xml," +
          encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
            '<rect width="100" height="100" fill="#eeeeee"/>' +
            '<circle cx="50" cy="36" r="18" fill="#777777"/>' +
            '<path d="M15 100 Q15 62 50 62 Q85 62 85 100" fill="#777777"/>' +
            '</svg>'
          );
      };
    }
  }

  if (profileDropdown) profileDropdown.hidden = true;
  if (profileButton) profileButton.setAttribute("aria-expanded", "false");
}

if (profileButton && profileDropdown) {
  profileButton.addEventListener("click", () => {
    const opening = profileDropdown.hidden;
    profileDropdown.hidden = !opening;
    profileButton.setAttribute("aria-expanded", String(opening));
  });
}

document.addEventListener("click", event => {
  if (profileContainer && !profileContainer.contains(event.target)) {
    if (profileDropdown) profileDropdown.hidden = true;
    if (profileButton) profileButton.setAttribute("aria-expanded", "false");
  }
});

if (logoutButton) {
  logoutButton.addEventListener("click", async () => {
    if (window.CredoraAuth && typeof window.CredoraAuth.logout === 'function') {
      await window.CredoraAuth.logout();
    } else {
      localStorage.removeItem("credoraUser");
    }
    updateNavbar();
    window.location.href = "login.html";
  });
}

// React to auth changes across windows or scripts
window.addEventListener("credora:auth-change", () => {
  updateNavbar();
});

window.addEventListener("storage", event => {
  if (event.key === "credoraUser" || event.key === null) {
    updateNavbar();
  }
});

// Sync initial session on page load
if (window.CredoraAuth && typeof window.CredoraAuth.checkSession === 'function') {
  window.CredoraAuth.checkSession().then(() => updateNavbar());
} else {
  updateNavbar();
}

const lenders = [
  {
    id: "northstar",
    name: "Northstar Finance",
    initials: "NF",
    type: "Personal & Education",
    description: "Sample personal and education loan profile.",
    minRate: 10.5,
    maxRate: 16.5,
    minAmount: 25000,
    maxAmount: 500000,
    tenure: "6–60 months",
    policy: "This is a demonstration policy, not a real loan offer. Confirm the actual interest calculation, fees, repayment dates, late charges, eligibility rules, and cancellation conditions directly with the lender before accepting an offer.",
    eligibility: [
      "Applicant must meet the lender's minimum age requirement.",
      "Income and repayment capacity may be reviewed.",
      "Submitted information must be accurate and complete."
    ],
    documents: [
      "Identity proof",
      "Address proof",
      "Income proof or student fee documentation",
      "Bank statement, if required"
    ]
  },
  {
    id: "pioneer",
    name: "Pioneer Capital",
    initials: "PC",
    type: "Small Business",
    description: "Sample financing profile for business applicants.",
    minRate: 12,
    maxRate: 19,
    minAmount: 50000,
    maxAmount: 1500000,
    tenure: "12–48 months",
    policy: "This sample policy is for demonstration only. A real lender must disclose its actual eligibility criteria, interest calculation, fees, repayment schedule, and complaint contact details.",
    eligibility: [
      "Applicant must provide a clear loan purpose.",
      "Income and business cash flow may be reviewed.",
      "Additional documentation may be required."
    ],
    documents: [
      "Identity proof",
      "Address proof",
      "Bank statements",
      "Business registration or income records, if applicable"
    ]
  },
  {
    id: "harbor",
    name: "Harbor Credit Union",
    initials: "HC",
    type: "Vehicle & Personal",
    description: "Sample profile for vehicle and personal loans.",
    minRate: 9.9,
    maxRate: 15.9,
    minAmount: 40000,
    maxAmount: 800000,
    tenure: "12–72 months",
    policy: "Sample policy only. Actual rates, collateral requirements, fees, eligibility and repayment terms must be confirmed in the lender's official documents.",
    eligibility: [
      "Applicant must meet the lender's eligibility criteria.",
      "Repayment capacity may be reviewed.",
      "Collateral requirements must be disclosed before applying."
    ],
    documents: [
      "Identity proof",
      "Address proof",
      "Income proof",
      "Vehicle quotation or purpose-related document, if applicable"
    ]
  }
];

const STORAGE_KEY = "credora_demo_applications_v1";
const $ = selector => document.querySelector(selector);
let selectedLenderId = null;

const escapeHTML = value =>
  String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[character]);

const money = value =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0
  }).format(Number(value) || 0);

function getApplications() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveApplications(applications) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(applications));
}

function getLender(id) {
  return lenders.find(lender => lender.id === id);
}

function notify(message) {
  const old = $(".toast");
  if (old) old.remove();

  const toast = document.createElement("div");
  toast.className = "toast";
  toast.setAttribute("role", "status");
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => toast.remove(), 3500);
}

function renderLenders(search = "") {
  const query = search.trim().toLowerCase();

  const results = lenders.filter(lender =>
    `${lender.name} ${lender.type} ${lender.description}`
      .toLowerCase().includes(query)
  );

  $("#lenderList").innerHTML = results.length
    ? results.map(lender => `
      <article class="lender-card">
        <div class="card-top">
          <div class="lender-mark">${escapeHTML(lender.initials)}</div>
          <span class="tag">${escapeHTML(lender.type)}</span>
        </div>

        <h3>${escapeHTML(lender.name)}</h3>
        <p class="description">${escapeHTML(lender.description)}</p>

        <div class="stats">
          <div class="stat">
            <strong>${lender.minRate}%–${lender.maxRate}%</strong>
            <span>SAMPLE ANNUAL RATE</span>
          </div>
          <div class="stat">
            <strong>${money(lender.minAmount)}–${money(lender.maxAmount)}</strong>
            <span>SAMPLE LOAN RANGE</span>
          </div>
          <div class="stat">
            <strong>${escapeHTML(lender.tenure)}</strong>
            <span>TENURE RANGE</span>
          </div>
          <div class="stat">
            <strong>Policy first</strong>
            <span>REVIEW REQUIRED</span>
          </div>
        </div>

        <div class="card-footer">
          <button class="button dark" data-policy="${escapeHTML(lender.id)}">
            VIEW POLICY ↗
          </button>
        </div>
      </article>
    `).join("")
    : `<div class="empty">No lenders found. Try another search.</div>`;
}

function openPolicy(id) {
  const lender = getLender(id);
  if (!lender) return;

  selectedLenderId = id;

  $("#policyContent").innerHTML = `
    <span class="tag">${escapeHTML(lender.type)}</span>
    <h2>${escapeHTML(lender.name)}</h2>
    <p class="policy-copy">${escapeHTML(lender.policy)}</p>

    <div class="detail-grid">
      <div class="detail-item">
        <span>SAMPLE ANNUAL RATE</span>
        <strong>${lender.minRate}%–${lender.maxRate}%</strong>
      </div>
      <div class="detail-item">
        <span>SAMPLE LOAN RANGE</span>
        <strong>${money(lender.minAmount)}–${money(lender.maxAmount)}</strong>
      </div>
    </div>

    <h3>Eligibility criteria</h3>
    <ul class="policy-list">
      ${lender.eligibility.map(item => `<li>${escapeHTML(item)}</li>`).join("")}
    </ul>

    <h3>Required documents</h3>
    <ul class="policy-list">
      ${lender.documents.map(item => `<li>${escapeHTML(item)}</li>`).join("")}
    </ul>

    <p class="disclaimer">
      All lender names, rates, and terms are fictional demonstration data.
      Confirm official terms before making financial decisions.
    </p>
  `;

  $("#policyDialog").showModal();
}

function openApplicationForm(id) {
  const lender = getLender(id);
  if (!lender) return;

  const form = $("#loanForm");
  form.reset();
  form.elements.lenderId.value = id;

  $("#formHeading").innerHTML = `
    <h2>${escapeHTML(lender.name)}</h2>
    <p class="policy-copy">
      Complete the form below. The lender may request further details
      during review.
    </p>
  `;

  $("#requiredDocuments").innerHTML = `
    <strong>Documents that may be requested</strong>
    <ul>${lender.documents.map(doc => `<li>${escapeHTML(doc)}</li>`).join("")}</ul>
    <small>This demo does not securely upload files. Do not enter sensitive
    identity numbers or bank credentials.</small>
  `;

  form.elements.amount.min = lender.minAmount;
  form.elements.amount.max = lender.maxAmount;
  form.elements.amount.placeholder =
    `${lender.minAmount}–${lender.maxAmount}`;

  form.elements.tenure.placeholder = lender.tenure;
  form.elements.dob.max = new Date().toISOString().slice(0, 10);

  $("#formDialog").showModal();
}

/* Demonstration calculations only — this is NOT an AI model. */
function makeDemoAnalysis(app, lender) {
  const income = Number(app.income) || 0;
  const debt = Number(app.existingDebt) || 0;
  const amount = Number(app.amount) || 0;
  const flags = [];

  if (income <= 0) {
    flags.push("Income is zero or missing; clarify the applicant's income source.");
  }

  if (amount < lender.minAmount || amount > lender.maxAmount) {
    flags.push("Requested amount is outside this sample lender's range.");
  }

  if (debt > income) {
    flags.push("Reported monthly debt payments exceed reported monthly income.");
  }

  if (!app.notes) {
    flags.push("No additional information was provided; check if further details are needed.");
  }

  const ratio = income > 0 ? (amount / income).toFixed(1) : null;

  return {
    summary: `${app.fullName} applied to ${lender.name} for ${money(amount)} for ${app.purpose}. Reported monthly income: ${money(income)}. Existing monthly debt payments: ${money(debt)}. Preferred tenure: ${app.tenure} months.`,
    observations: [
      `Income remaining after reported existing debt payments: ${money(income - debt)}. This is only a simple arithmetic indicator.`,
      ratio
        ? `Requested loan amount equals ${ratio} times the reported monthly income.`
        : "A requested-amount-to-income ratio cannot be calculated because the income is zero.",
      `Reported employment type: ${app.employment}. This information has not been verified.`
    ],
    flags,
    recommendation: "A qualified, authorized lender must verify the documents, apply the real policy, and make the final decision.",
    disclaimer: "Local rule-based demo analysis only. It is not an AI model, credit score, document verification, or financial advice."
  };
}

function renderCustomerApplications() {
  const apps = [...getApplications()].reverse();

  $("#myApplications").innerHTML = apps.length
    ? apps.map(app => `
      <article class="application-row">
        <div>
          <h3>${escapeHTML(app.lenderName)} · ${money(app.amount)}</h3>
          <div class="row-meta">
            REF ${escapeHTML(app.id)}<br>
            ${escapeHTML(new Date(app.createdAt).toLocaleString())}
          </div>
        </div>
        <div class="row-actions">
          <span class="status ${escapeHTML(app.status)}">
            ${escapeHTML(app.status.toUpperCase())}
          </span>
          <button class="button" data-details="${escapeHTML(app.id)}">
            VIEW STATUS
          </button>
        </div>
      </article>
    `).join("")
    : `<div class="empty">No applications yet. Choose a lender, read the policy, and apply.</div>`;
}

function renderReviewApplications() {
  const apps = [...getApplications()].reverse();

  $("#reviewApplications").innerHTML = apps.length
    ? apps.map(app => `
      <article class="application-row">
        <div>
          <h3>${escapeHTML(app.fullName)} · ${money(app.amount)}</h3>
          <div class="row-meta">
            ${escapeHTML(app.lenderName)} · ${escapeHTML(app.purpose)}<br>
            REF ${escapeHTML(app.id)} · ${escapeHTML(app.email)}
          </div>
        </div>

        <div class="row-actions">
          <span class="status ${escapeHTML(app.status)}">
            ${escapeHTML(app.status.toUpperCase())}
          </span>
          <button class="button" data-details="${escapeHTML(app.id)}">
            REVIEW
          </button>
          ${app.status === "pending" ? `
            <button class="button dark"
                    data-decision="accepted"
                    data-id="${escapeHTML(app.id)}">ACCEPT</button>
            <button class="button"
                    data-decision="rejected"
                    data-id="${escapeHTML(app.id)}">REJECT</button>
          ` : ""}
        </div>
      </article>
    `).join("")
    : `<div class="empty">Submitted demo applications will appear here.</div>`;
}

function openDetails(id) {
  const app = getApplications().find(item => item.id === id);
  if (!app) return;

  const lender = getLender(app.lenderId);
  if (!lender) return;

  const analysis = app.analysis || makeDemoAnalysis(app, lender);

  const fields = [
    ["REFERENCE", app.id],
    ["SUBMITTED", new Date(app.createdAt).toLocaleString()],
    ["EMAIL", app.email],
    ["PHONE", app.phone],
    ["DATE OF BIRTH", app.dob],
    ["REQUESTED AMOUNT", money(app.amount)],
    ["MONTHLY INCOME", money(app.income)],
    ["EMPLOYMENT", app.employment],
    ["EXISTING MONTHLY DEBT", money(app.existingDebt)],
    ["REQUESTED TENURE", `${app.tenure} months`],
    ["ADDRESS", app.address],
    ["ADDITIONAL DETAILS", app.notes || "Not provided"]
  ];

  $("#detailsContent").innerHTML = `
    <div class="card-top">
      <div>
        <h2>${escapeHTML(app.fullName)}</h2>
        <p class="policy-copy">${escapeHTML(app.lenderName)}</p>
      </div>
      <span class="status ${escapeHTML(app.status)}">
        ${escapeHTML(app.status.toUpperCase())}
      </span>
    </div>

    <div class="detail-grid">
      ${fields.map(([label, value]) => `
        <div class="detail-item">
          <span>${escapeHTML(label)}</span>
          <strong>${escapeHTML(value)}</strong>
        </div>
      `).join("")}
    </div>

    <section class="analysis">
      <h3>AI-STYLE SUMMARY & ANALYSIS</h3>
      <p>${escapeHTML(analysis.summary)}</p>

      <h3>Observations</h3>
      <ul>${analysis.observations.map(item =>
        `<li>${escapeHTML(item)}</li>`).join("")}</ul>

      <h3>Items to clarify</h3>
      ${analysis.flags.length
        ? `<ul>${analysis.flags.map(item =>
            `<li>${escapeHTML(item)}</li>`).join("")}</ul>`
        : "<p>No simple rule-based flags were detected. This does not mean the application is verified or eligible.</p>"}

      <p>${escapeHTML(analysis.recommendation)}</p>
      <p class="disclaimer">${escapeHTML(analysis.disclaimer)}</p>
    </section>
  `;

  $("#detailsDialog").showModal();
}

function decideApplication(id, status) {
  const apps = getApplications();
  const app = apps.find(item => item.id === id);

  if (!app || !["accepted", "rejected"].includes(status)) return;

  if (app.status !== "pending") {
    notify("This application already has a decision.");
    return;
  }

  const action = status === "accepted" ? "accept" : "reject";

  if (!confirm(`Confirm ${action} for application ${id}?`)) return;

  app.status = status;
  app.decisionAt = new Date().toISOString();
  saveApplications(apps);

  renderCustomerApplications();
  renderReviewApplications();

  notify(`Demo status changed to ${status.toUpperCase()}. No customer notification was sent.`);
}

/* Event handlers */
$("#search").addEventListener("input", event => {
  renderLenders(event.target.value);
});

$("#lenderList").addEventListener("click", event => {
  const button = event.target.closest("[data-policy]");
  if (button) openPolicy(button.dataset.policy);
});

$("#applyButton").addEventListener("click", () => {
  const id = selectedLenderId;
  $("#policyDialog").close();
  openApplicationForm(id);
});

document.querySelectorAll("[data-close]").forEach(button => {
  button.addEventListener("click", () => {
    document.getElementById(button.dataset.close).close();
  });
});

document.querySelectorAll("dialog").forEach(dialog => {
  dialog.addEventListener("click", event => {
    if (event.target === dialog) dialog.close();
  });
});

$("#loanForm").addEventListener("submit", event => {
  event.preventDefault();

  const form = event.currentTarget;
  if (!form.reportValidity()) return;

  const data = new FormData(form);
  const lender = getLender(String(data.get("lenderId")));
  if (!lender) return notify("Please select a valid lender.");

  const app = {
    id: `CR-${Date.now().toString().slice(-8)}`,
    lenderId: lender.id,
    lenderName: lender.name,
    fullName: String(data.get("fullName")).trim(),
    email: String(data.get("email")).trim(),
    phone: String(data.get("phone")).trim(),
    dob: String(data.get("dob")),
    amount: Number(data.get("amount")),
    purpose: String(data.get("purpose")),
    income: Number(data.get("income")),
    employment: String(data.get("employment")),
    existingDebt: Number(data.get("existingDebt")),
    tenure: Number(data.get("tenure")),
    address: String(data.get("address")).trim(),
    notes: String(data.get("notes") || "").trim(),
    status: "pending",
    createdAt: new Date().toISOString()
  };

  if (app.amount < lender.minAmount || app.amount > lender.maxAmount) {
    return notify(`Amount must be between ${money(lender.minAmount)} and ${money(lender.maxAmount)} in this demo.`);
  }

  if (app.tenure < 1 || app.tenure > 360) {
    return notify("Enter a valid repayment tenure.");
  }

  app.analysis = makeDemoAnalysis(app, lender);

  const apps = getApplications();
  apps.push(app);
  saveApplications(apps);

  form.reset();
  $("#formDialog").close();

  renderCustomerApplications();
  renderReviewApplications();

  notify(`Application ${app.id} saved in this browser only.`);
  $("#myApplications").scrollIntoView({ behavior: "smooth" });
});

$("#myApplications").addEventListener("click", event => {
  const button = event.target.closest("[data-details]");
  if (button) openDetails(button.dataset.details);
});

$("#reviewApplications").addEventListener("click", event => {
  const button = event.target.closest("[data-details]");
  if (button) {
    openDetails(button.dataset.details);
    return;
  }

  const decision = event.target.closest("[data-decision]");
  if (decision) {
    decideApplication(decision.dataset.id, decision.dataset.decision);
  }
});

$("#year").textContent = new Date().getFullYear();

renderLenders();
renderCustomerApplications();
renderReviewApplications();

/*
 * CREDORA lender directory
 *
 * Current mode: Supabase is not configured.
 * Future mode: retrieve approved, active lender records from Supabase.
 */

const LenderDirectory = (() => {
  const searchInput = document.getElementById("lenderSearch");
  const lenderList = document.getElementById("lenderList");
  const lenderCount = document.getElementById("lenderCount");
  const emptyMessage = document.getElementById("emptyMessage");
  const errorMessage = document.getElementById("errorMessage");

  let lenders = [];

  function renderLenders(searchTerm = "") {
    const query = searchTerm.trim().toLowerCase();

    const filtered = lenders.filter(lender =>
      typeof lender.name === "string" &&
      lender.name.toLowerCase().includes(query)
    );

    lenderCount.textContent =
      `${filtered.length} ${filtered.length === 1 ? "LENDER" : "LENDERS"} AVAILABLE`;

    lenderList.replaceChildren();

    filtered.forEach((lender, index) => {
      const item = document.createElement("li");
      item.className = "lender-item";

      const number = document.createElement("span");
      number.className = "lender-number";
      number.textContent = String(index + 1).padStart(2, "0");

      item.appendChild(number);
      item.appendChild(document.createTextNode(lender.name));
      lenderList.appendChild(item);
    });

    emptyMessage.hidden = filtered.length !== 0;
  }

  function showError() {
    lenders = [];
    renderLenders(searchInput.value);
    emptyMessage.hidden = true;
    errorMessage.hidden = false;
  }

  function showLenders(records) {
    errorMessage.hidden = true;

    // Accept only explicitly verified and active records.
    lenders = records.filter(record =>
      record.is_active === true &&
      record.is_verified === true &&
      typeof record.name === "string" &&
      record.name.trim().length > 0
    );

    renderLenders(searchInput.value);
  }

  searchInput.addEventListener("input", () => {
    renderLenders(searchInput.value);
  });

  document.getElementById("currentYear").textContent =
    new Date().getFullYear();

  /*
   * Supabase has not been connected yet.
   * Do not use sample lender names as real records.
   */
  renderLenders();

  return {
    showLenders,
    showError,
    renderLenders
  };
})();

