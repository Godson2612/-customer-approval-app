(function () {
  const STORAGE_KEYS = {
    supervisor: "opsToolkit.supervisor",
    techNumber: "opsToolkit.techNumber",
    location: "opsToolkit.location",
  };

  document.addEventListener("DOMContentLoaded", () => {
    initHomePreferences();
    initWorkflow();
  });

  function initHomePreferences() {
    const homeScreen = document.querySelector(".home-screen");
    if (!homeScreen) return;

    const supervisorInput = document.getElementById("prefSupervisor");
    const techNumberInput = document.getElementById("prefTechNumber");
    const locationInput = document.getElementById("prefLocation");
    const preferencesForm = document.getElementById("preferencesForm");
    const clearButton = document.getElementById("clearPreferencesButton");
    const status = document.getElementById("preferencesStatus");

    if (!supervisorInput || !techNumberInput || !locationInput || !preferencesForm) return;

    const defaultSupervisor = homeScreen.dataset.defaultSupervisor || "";
    const defaultTechNumber = homeScreen.dataset.defaultTechNumber || "";
    const defaultLocation = homeScreen.dataset.defaultLocation || "";

    supervisorInput.value = loadPreference(STORAGE_KEYS.supervisor, defaultSupervisor);
    techNumberInput.value = loadPreference(STORAGE_KEYS.techNumber, defaultTechNumber);
    locationInput.value = loadPreference(STORAGE_KEYS.location, defaultLocation);

    preferencesForm.addEventListener("submit", (event) => {
      event.preventDefault();

      savePreference(STORAGE_KEYS.supervisor, supervisorInput.value.trim());
      savePreference(STORAGE_KEYS.techNumber, techNumberInput.value.trim());
      savePreference(STORAGE_KEYS.location, locationInput.value.trim());

      if (status) {
        status.hidden = false;
        status.innerHTML =
          '<div class="status-card is-success"><strong>Preferences saved</strong><p>Supervisor, Tech Number, and default Location are now saved on this device.</p></div>';
      }
    });

    if (clearButton) {
      clearButton.addEventListener("click", () => {
        removePreference(STORAGE_KEYS.supervisor);
        removePreference(STORAGE_KEYS.techNumber);
        removePreference(STORAGE_KEYS.location);

        supervisorInput.value = "";
        techNumberInput.value = "";
        locationInput.value = "";

        if (status) {
          status.hidden = false;
          status.innerHTML =
            '<div class="status-card is-warning"><strong>Preferences cleared</strong><p>Saved EPON values were removed from this device.</p></div>';
        }
      });
    }
  }

  function initWorkflow() {
    const workflowScreen = document.querySelector(".workflow-screen");
    if (!workflowScreen) return;

    const workflowType = workflowScreen.dataset.workflow || "customer-approval";
    if (workflowType === "epon") {
      initEponWorkflow(workflowScreen);
      return;
    }

    initCustomerApprovalWorkflow(workflowScreen);
  }

  function initCustomerApprovalWorkflow(workflowScreen) {
    const state = {
      step: 1,
      fields: {},
      confidence: {},
      warnings: [],
      screenshotFilename: null,
      extractionJson: {},
      keepScreenshot: false,
      downloadUrl: "",
      shareMeta: null,
      signatures: { customer: null, technician: null },
      preparedBlob: null,
      preparedFileName: "",
      previewUrl: null,
    };

    const elements = {
      statusBanner: document.getElementById("statusBanner"),
      extractForm: document.getElementById("extractForm"),
      reviewForm: document.getElementById("reviewForm"),
      screenshotInput: document.getElementById("screenshotInput"),
      imagePreview: document.getElementById("imagePreview"),
      previewImage: document.getElementById("previewImage"),
      uploadStatus: document.getElementById("uploadStatus"),
      dropzone: document.querySelector(".upload-dropzone"),
      reviewWarnings: document.getElementById("reviewWarnings"),
      stickyButton: document.getElementById("stickyPrimaryButton"),
      stickyTitle: document.getElementById("stickyTitle"),
      stickySubtitle: document.getElementById("stickySubtitle"),
      keepScreenshot: document.getElementById("keepScreenshot"),
      shareButton: document.getElementById("shareButton"),
      restartButton: document.getElementById("restartButton"),
      downloadButton: document.getElementById("downloadButton"),
      successMessage: document.getElementById("successMessage"),
    };

    const cardsByStep = new Map(
      Array.from(document.querySelectorAll("[data-step]")).map((node) => [
        Number(node.dataset.step),
        node,
      ])
    );

    const progressSteps = Array.from(document.querySelectorAll("[data-progress-step]"));

    const reviewFields = [
      "job_number",
      "customer_name",
      "service_address",
      "city_state_zip",
      "phone_number",
      "work_phone_number",
      "email",
      "installation_date",
      "technician_name",
    ];

    const requiredFields = [
      "job_number",
      "customer_name",
      "service_address",
      "city_state_zip",
      "phone_number",
      "installation_date",
      "technician_name",
    ];

    const reviewInputs = Object.fromEntries(
      reviewFields
        .map((name) => [name, document.querySelector(`[name="${name}"]`)])
        .filter(([, input]) => input)
    );

    const installationDateMirror = document.getElementById("installationDateDuplicate");
    const initialTechnicianInput = document.getElementById("technicianNameInitial");
    const customerPad = createSignaturePad(document.getElementById("customerSignaturePad"));
    const technicianPad = createSignaturePad(document.getElementById("technicianSignaturePad"));

    bindEvents();
    syncInitialValues();
    setStep(1);

    function bindEvents() {
      if (elements.screenshotInput) {
        elements.screenshotInput.addEventListener("change", handleFileSelection);
      }

      if (elements.extractForm) {
        elements.extractForm.addEventListener("submit", (event) => {
          event.preventDefault();
          extractInformation();
        });
      }

      if (elements.reviewForm) {
        elements.reviewForm.addEventListener("submit", (event) => {
          event.preventDefault();
          continueFromReview();
        });
      }

      Object.values(reviewInputs).forEach((input) => {
        input.addEventListener("input", () => {
          if (input.name === "technician_name" && initialTechnicianInput) {
            initialTechnicianInput.value = input.value;
          }

          if (input.name === "installation_date" && installationDateMirror) {
            installationDateMirror.value = input.value;
          }

          input.classList.remove("is-invalid");
          updateStickyAction();
        });
      });

      if (installationDateMirror && reviewInputs.installation_date) {
        installationDateMirror.addEventListener("input", () => {
          reviewInputs.installation_date.value = installationDateMirror.value;
          reviewInputs.installation_date.dispatchEvent(new Event("input", { bubbles: true }));
        });
      }

      if (elements.keepScreenshot) {
        elements.keepScreenshot.addEventListener("change", () => {
          state.keepScreenshot = elements.keepScreenshot.checked;
        });
      }

      const clearCustomerButton = document.getElementById("clearCustomerSignature");
      if (clearCustomerButton) {
        clearCustomerButton.addEventListener("click", () => {
          customerPad.clear();
          state.signatures.customer = null;
          updateStickyAction();
        });
      }

      const clearTechnicianButton = document.getElementById("clearTechnicianSignature");
      if (clearTechnicianButton) {
        clearTechnicianButton.addEventListener("click", () => {
          technicianPad.clear();
          state.signatures.technician = null;
          updateStickyAction();
        });
      }

      customerPad.onChange = () => {
        state.signatures.customer = customerPad.isEmpty() ? null : customerPad.toDataURL();
        updateStickyAction();
      };

      technicianPad.onChange = () => {
        state.signatures.technician = technicianPad.isEmpty() ? null : technicianPad.toDataURL();
        updateStickyAction();
      };

      if (elements.stickyButton) {
        elements.stickyButton.addEventListener("click", handleStickyAction);
      }

      if (elements.shareButton) {
        elements.shareButton.addEventListener("click", shareDocument);
      }

      if (elements.restartButton) {
        elements.restartButton.addEventListener("click", restartFlow);
      }

      window.addEventListener("resize", () => {
        customerPad.resize();
        technicianPad.resize();
      });
    }

    function syncInitialValues() {
      const defaultTech = workflowScreen.dataset.defaultTech || "";
      const today = workflowScreen.dataset.today || "";

      if (reviewInputs.technician_name && !reviewInputs.technician_name.value) {
        reviewInputs.technician_name.value = defaultTech;
      }

      if (reviewInputs.installation_date && !reviewInputs.installation_date.value) {
        reviewInputs.installation_date.value = today;
      }

      if (installationDateMirror && reviewInputs.installation_date) {
        installationDateMirror.value = reviewInputs.installation_date.value || today;
      }

      if (initialTechnicianInput && !initialTechnicianInput.value) {
        initialTechnicianInput.value = defaultTech;
      }
    }

    async function handleFileSelection() {
      const file = elements.screenshotInput ? elements.screenshotInput.files[0] : null;

      if (elements.uploadStatus) {
        elements.uploadStatus.textContent = file ? file.name : "No file selected";
      }

      if (elements.dropzone) {
        elements.dropzone.classList.toggle("is-ready", Boolean(file));
      }

      clearPreparedFile(false);

      if (!file) {
        updateStickyAction();
        return;
      }

      if (!looksLikeImageFile(file)) {
        resetFileSelection();
        showStatus("error", "Image required", "Please choose a screenshot image.");
        return;
      }

      state.previewUrl = URL.createObjectURL(file);

      if (elements.previewImage) {
        elements.previewImage.src = state.previewUrl;
      }

      if (elements.imagePreview) {
        elements.imagePreview.hidden = false;
      }

      try {
        const normalized = await normalizeImageToPng(file);
        state.preparedBlob = normalized.blob;
        state.preparedFileName = normalized.name;
        if (elements.uploadStatus) {
          elements.uploadStatus.textContent = normalized.name;
        }
      } catch (error) {
        state.preparedBlob = file;
        state.preparedFileName = file.name || "screenshot-upload";
      }

      updateStickyAction();
      extractInformation();
    }

    function handleStickyAction() {
      if (state.step === 1) {
        extractInformation();
        return;
      }

      if (state.step === 3) {
        continueFromReview();
        return;
      }

      if (state.step === 4) {
        if (!state.signatures.customer) {
          showStatus("error", "Customer signature required", "Please capture the customer signature to continue.");
          return;
        }
        setStep(5);
        scrollToTopSmooth();
        return;
      }

      if (state.step === 5) {
        if (!state.signatures.technician) {
          showStatus("error", "Technician signature required", "Please capture the technician signature to continue.");
          return;
        }
        generateDocument();
        return;
      }

      if (state.step === 7 && state.downloadUrl) {
        window.location.href = state.downloadUrl;
      }
    }

    function continueFromReview() {
      if (!validateReviewForm()) return;
      syncReviewState();
      setStep(4);
      scrollToTopSmooth();
    }

    async function extractInformation() {
      if (!state.preparedBlob) {
        showStatus("error", "Screenshot required", "Select a screenshot before continuing.");
        updateStickyAction();
        return;
      }

      clearStatus();
      setStep(2);
      scrollToTopSmooth();

      const formData = new FormData();
      formData.append(
        "technician_name",
        initialTechnicianInput ? initialTechnicianInput.value.trim() : ""
      );

      if (elements.keepScreenshot && elements.keepScreenshot.checked) {
        formData.append("keep_screenshot", "true");
      }

      formData.append(
        "screenshot",
        state.preparedBlob,
        state.preparedFileName || "screenshot-upload.png"
      );

      state.keepScreenshot = elements.keepScreenshot ? elements.keepScreenshot.checked : false;

      try {
        const payload = await fetchJson("/api/customer-approval/extract", {
          method: "POST",
          headers: {
            "X-CSRF-Token": window.APP_CONFIG ? window.APP_CONFIG.csrfToken : "",
          },
          body: formData,
        });

        state.fields = payload.fields || {};
        state.confidence = payload.confidence || {};
        state.warnings = payload.warnings || [];
        state.screenshotFilename = payload.meta ? payload.meta.screenshot_filename : null;
        state.extractionJson = payload;

        populateReviewForm();
        renderWarnings();
        showStatus("success", "Information extracted", "Review the details below before collecting signatures.");
        setStep(3);
        scrollToTopSmooth();
      } catch (error) {
        setStep(1);
        showStatus(
          "error",
          "Extraction could not be completed",
          error && error.message ? String(error.message) : "Unable to extract information from the screenshot."
        );
        scrollToTopSmooth();
      }
    }

    function populateReviewForm() {
      Object.entries(reviewInputs).forEach(([name, input]) => {
        const value = typeof state.fields[name] === "string" ? state.fields[name] : "";

        if (value) {
          input.value = value;
        }

        if (name === "technician_name" && !input.value && initialTechnicianInput) {
          input.value = initialTechnicianInput.value.trim();
        }

        input.classList.remove("is-invalid");
      });

      if (installationDateMirror && reviewInputs.installation_date) {
        installationDateMirror.value = reviewInputs.installation_date.value;
      }

      document.querySelectorAll("[data-confidence-for]").forEach((node) => {
        const key = node.dataset.confidenceFor;
        const confidence = state.confidence[key];
        node.textContent =
          typeof confidence === "number" ? `Confidence ${Math.round(confidence * 100)}%` : "";
      });
    }

    function renderWarnings() {
      if (!elements.reviewWarnings) return;

      if (!state.warnings.length) {
        elements.reviewWarnings.hidden = true;
        elements.reviewWarnings.innerHTML = "";
        return;
      }

      elements.reviewWarnings.hidden = false;
      const items = state.warnings
        .map((warning) => `<li>${escapeHtml(warning)}</li>`)
        .join("");

      elements.reviewWarnings.innerHTML =
        `<div class="status-card is-warning"><strong>Review recommended</strong><ul>${items}</ul></div>`;
    }

    function validateReviewForm() {
      syncReviewState();
      let isValid = true;

      requiredFields.forEach((name) => {
        const input = reviewInputs[name];
        const hasValue = Boolean(input && input.value.trim());

        if (input) {
          input.classList.toggle("is-invalid", !hasValue);
        }

        isValid = isValid && hasValue;
      });

      if (!isValid) {
        showStatus("error", "Required fields are missing", "Complete all required fields before continuing.");
        scrollToFirstInvalidField();
      } else {
        clearStatus();
      }

      updateStickyAction();
      return isValid;
    }

    function syncReviewState() {
      Object.entries(reviewInputs).forEach(([name, input]) => {
        state.fields[name] = input.value.trim();
      });
    }

    async function generateDocument() {
      syncReviewState();
      setStep(6);
      clearStatus();
      scrollToTopSmooth();

      const payload = {
        fields: {
          ...state.fields,
          customer_signature: state.signatures.customer || "",
          technician_signature: state.signatures.technician || "",
        },
        screenshot_filename: state.screenshotFilename,
        delete_screenshot_after: !state.keepScreenshot,
        extraction_json: state.extractionJson,
      };

      try {
        const result = await fetchJson("/api/customer-approval/generate", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": window.APP_CONFIG ? window.APP_CONFIG.csrfToken : "",
          },
          body: JSON.stringify(payload),
        });

        state.downloadUrl = result.download_url;
        state.shareMeta = result.share || null;

        if (elements.downloadButton) {
          elements.downloadButton.href = result.download_url;
        }

        if (elements.successMessage) {
          elements.successMessage.textContent =
            result.message || "The approval PDF is ready for download or sharing.";
        }

        setStep(7);
        showStatus("success", "Document ready", "The approval PDF has been generated successfully.");
        scrollToTopSmooth();
      } catch (error) {
        setStep(5);
        showStatus(
          "error",
          "Document generation failed",
          error && error.message ? String(error.message) : "Unable to generate the document."
        );
        scrollToTopSmooth();
      }
    }

    async function shareDocument() {
      if (!state.downloadUrl) return;

      if (navigator.share) {
        try {
          await navigator.share({
            title: state.shareMeta && state.shareMeta.title ? state.shareMeta.title : "Customer Approval",
            text: "Customer approval document is ready.",
            url: `${window.location.origin}${state.downloadUrl}`,
          });
          return;
        } catch (error) {
          if (error && error.name === "AbortError") return;
        }
      }

      window.location.href = state.downloadUrl;
    }

    function restartFlow() {
      state.step = 1;
      state.fields = {};
      state.confidence = {};
      state.warnings = [];
      state.screenshotFilename = null;
      state.extractionJson = {};
      state.downloadUrl = "";
      state.shareMeta = null;
      state.signatures.customer = null;
      state.signatures.technician = null;

      if (elements.extractForm) elements.extractForm.reset();
      if (elements.reviewForm) elements.reviewForm.reset();
      if (elements.uploadStatus) elements.uploadStatus.textContent = "No file selected";
      if (elements.dropzone) elements.dropzone.classList.remove("is-ready");
      if (elements.reviewWarnings) {
        elements.reviewWarnings.hidden = true;
        elements.reviewWarnings.innerHTML = "";
      }

      clearPreparedFile(false);
      clearStatus();
      customerPad.clear();
      technicianPad.clear();
      syncInitialValues();
      setStep(1);
      scrollToTopSmooth();
    }

    function setStep(step) {
      state.step = step;

      cardsByStep.forEach((node, nodeStep) => {
        node.hidden = nodeStep !== step;
      });

      progressSteps.forEach((node) => {
        const nodeStep = Number(node.dataset.progressStep);
        const current = progressStepFor(step);
        node.classList.toggle("is-active", nodeStep === current);
        node.classList.toggle("is-complete", nodeStep < current);
      });

      if (step === 4) customerPad.resize();
      if (step === 5) technicianPad.resize();

      updateStickyAction();
    }

    function progressStepFor(step) {
      if (step <= 2) return 1;
      if (step === 3) return 3;
      if (step === 4) return 4;
      if (step === 5) return 5;
      return 6;
    }

    function updateStickyAction() {
      if (!elements.stickyTitle || !elements.stickySubtitle || !elements.stickyButton) return;

      let title = "Upload Screenshot";
      let subtitle = "Select a screenshot to continue.";
      let label = "Extract Information";
      let disabled = false;

      if (state.step === 1) {
        subtitle = state.preparedBlob ? "Ready to extract information." : "Select a screenshot to continue.";
        disabled = !state.preparedBlob;
      } else if (state.step === 2) {
        title = "Extracting Information";
        subtitle = "Please wait while the screenshot is processed.";
        label = "Processing";
        disabled = true;
      } else if (state.step === 3) {
        title = "Review Details";
        subtitle = "Confirm all required fields before continuing.";
        label = "Continue";
        disabled = !requiredFields.every(
          (name) => reviewInputs[name] && reviewInputs[name].value.trim()
        );
      } else if (state.step === 4) {
        title = "Customer Signature";
        subtitle = state.signatures.customer ? "Signature captured and ready to continue." : "Customer signature is required.";
        label = "Continue";
        disabled = !state.signatures.customer;
      } else if (state.step === 5) {
        title = "Technician Signature";
        subtitle = state.signatures.technician ? "Ready to generate the final document." : "Technician signature is required.";
        label = "Generate Document";
        disabled = !state.signatures.technician;
      } else if (state.step === 6) {
        title = "Generating Document";
        subtitle = "Finalizing the approval PDF.";
        label = "Generating";
        disabled = true;
      } else if (state.step === 7) {
        title = "Document Ready";
        subtitle = "Download the approval PDF or start a new approval.";
        label = "Download PDF";
        disabled = !state.downloadUrl;
      }

      elements.stickyTitle.textContent = title;
      elements.stickySubtitle.textContent = subtitle;
      elements.stickyButton.textContent = label;
      elements.stickyButton.disabled = disabled;
    }

    function clearPreparedFile(clearInput = true) {
      state.preparedBlob = null;
      state.preparedFileName = "";

      if (state.previewUrl) {
        URL.revokeObjectURL(state.previewUrl);
        state.previewUrl = null;
      }

      if (elements.imagePreview) {
        elements.imagePreview.hidden = true;
      }

      if (elements.previewImage) {
        elements.previewImage.removeAttribute("src");
      }

      if (clearInput && elements.screenshotInput) {
        elements.screenshotInput.value = "";
      }
    }

    function resetFileSelection() {
      if (elements.uploadStatus) {
        elements.uploadStatus.textContent = "No file selected";
      }

      if (elements.dropzone) {
        elements.dropzone.classList.remove("is-ready");
      }

      clearPreparedFile(true);
      updateStickyAction();
    }

    function showStatus(type, title, message) {
      if (!elements.statusBanner) return;
      elements.statusBanner.innerHTML =
        `<div class="status-card is-${type}"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
    }

    function clearStatus() {
      if (elements.statusBanner) elements.statusBanner.innerHTML = "";
    }
  }

  function initEponWorkflow(workflowScreen) {
    const state = {
      step: 1,
      fields: {},
      confidence: {},
      warnings: [],
      screenshotFilename: null,
      extractionJson: {},
      keepScreenshot: false,
      externalUrl: "https://techops.cuicable.com/index.php/epon-additional-billing/",
      preparedBlob: null,
      preparedFileName: "",
      previewUrl: null,
    };

    const elements = {
      statusBanner: document.getElementById("statusBanner"),
      extractForm: document.getElementById("extractForm"),
      reviewForm: document.getElementById("reviewForm"),
      screenshotInput: document.getElementById("screenshotInput"),
      imagePreview: document.getElementById("imagePreview"),
      previewImage: document.getElementById("previewImage"),
      uploadStatus: document.getElementById("uploadStatus"),
      dropzone: document.querySelector(".upload-dropzone"),
      reviewWarnings: document.getElementById("reviewWarnings"),
      stickyButton: document.getElementById("stickyPrimaryButton"),
      stickyTitle: document.getElementById("stickyTitle"),
      stickySubtitle: document.getElementById("stickySubtitle"),
      keepScreenshot: document.getElementById("keepScreenshot"),
      restartButton: document.getElementById("restartButton"),
      successMessage: document.getElementById("successMessage"),
      openExternalButton: document.getElementById("openExternalButton"),
      techNumberInitial: document.getElementById("techNumberInitial"),
      supervisorInitial: document.getElementById("supervisorInitial"),
      locationInitial: document.getElementById("locationInitial"),
      billingType: document.getElementById("billingType"),
      rr8QuantityField: document.getElementById("rr8QuantityField"),
      rs3QuantityField: document.getElementById("rs3QuantityField"),
      rr8Quantity: document.getElementById("rr8Quantity"),
      rs3Quantity: document.getElementById("rs3Quantity"),
    };

    const cardsByStep = new Map(
      Array.from(document.querySelectorAll("[data-step]")).map((node) => [
        Number(node.dataset.step),
        node,
      ])
    );

    const progressSteps = Array.from(document.querySelectorAll("[data-progress-step]"));

    const reviewFields = [
      "billing_date",
      "location",
      "tech_number",
      "supervisor",
      "customer_name",
      "customer_address",
      "address_line_2",
      "city",
      "state",
      "postal",
      "account_number",
      "job_number",
      "primary_phone",
      "billing_type",
      "rr8_quantity",
      "rs3_quantity",
    ];

    const requiredFields = [
      "billing_date",
      "location",
      "tech_number",
      "supervisor",
      "customer_address",
      "city",
      "state",
      "postal",
      "account_number",
      "billing_type",
    ];

    const reviewInputs = Object.fromEntries(
      reviewFields
        .map((name) => [name, document.querySelector(`[name="${name}"]`)])
        .filter(([, input]) => input)
    );

    bindEvents();
    syncInitialValues();
    toggleBillingQuantityFields();
    setStep(1);

    function bindEvents() {
      if (elements.screenshotInput) {
        elements.screenshotInput.addEventListener("change", handleFileSelection);
      }

      if (elements.extractForm) {
        elements.extractForm.addEventListener("submit", (event) => {
          event.preventDefault();
          extractInformation();
        });
      }

      if (elements.reviewForm) {
        elements.reviewForm.addEventListener("submit", (event) => {
          event.preventDefault();
          submitRequest();
        });
      }

      if (elements.billingType) {
        elements.billingType.addEventListener("change", () => {
          toggleBillingQuantityFields();
          updateStickyAction();
        });
      }

      Object.values(reviewInputs).forEach((input) => {
        input.addEventListener("input", () => {
          input.classList.remove("is-invalid");
          updateStickyAction();
        });
        input.addEventListener("change", () => {
          input.classList.remove("is-invalid");
          updateStickyAction();
        });
      });

      if (elements.keepScreenshot) {
        elements.keepScreenshot.addEventListener("change", () => {
          state.keepScreenshot = elements.keepScreenshot.checked;
        });
      }

      if (elements.stickyButton) {
        elements.stickyButton.addEventListener("click", handleStickyAction);
      }

      if (elements.restartButton) {
        elements.restartButton.addEventListener("click", restartFlow);
      }
    }

    function syncInitialValues() {
      const defaultTechNumber = loadPreference(
        STORAGE_KEYS.techNumber,
        workflowScreen.dataset.defaultTechNumber || ""
      );
      const defaultSupervisor = loadPreference(
        STORAGE_KEYS.supervisor,
        workflowScreen.dataset.defaultSupervisor || ""
      );
      const defaultLocation = loadPreference(
        STORAGE_KEYS.location,
        workflowScreen.dataset.defaultLocation || ""
      );
      const today = workflowScreen.dataset.today || "";

      if (elements.techNumberInitial && !elements.techNumberInitial.value) {
        elements.techNumberInitial.value = defaultTechNumber;
      } else if (elements.techNumberInitial) {
        elements.techNumberInitial.value = defaultTechNumber || elements.techNumberInitial.value;
      }

      if (elements.supervisorInitial && !elements.supervisorInitial.value) {
        elements.supervisorInitial.value = defaultSupervisor;
      } else if (elements.supervisorInitial) {
        elements.supervisorInitial.value = defaultSupervisor || elements.supervisorInitial.value;
      }

      if (elements.locationInitial && defaultLocation) {
        elements.locationInitial.value = defaultLocation;
      }

      if (reviewInputs.billing_date && !reviewInputs.billing_date.value) {
        reviewInputs.billing_date.value = today;
      }
      if (reviewInputs.tech_number && !reviewInputs.tech_number.value) {
        reviewInputs.tech_number.value = defaultTechNumber;
      }
      if (reviewInputs.supervisor && !reviewInputs.supervisor.value) {
        reviewInputs.supervisor.value = defaultSupervisor;
      }
      if (reviewInputs.location && defaultLocation) {
        reviewInputs.location.value = defaultLocation;
      }
      if (reviewInputs.state && !reviewInputs.state.value) {
        reviewInputs.state.value = "FL";
      }
    }

    async function handleFileSelection() {
      const file = elements.screenshotInput ? elements.screenshotInput.files[0] : null;

      if (elements.uploadStatus) {
        elements.uploadStatus.textContent = file ? file.name : "No file selected";
      }

      if (elements.dropzone) {
        elements.dropzone.classList.toggle("is-ready", Boolean(file));
      }

      clearPreparedFile(false);

      if (!file) {
        updateStickyAction();
        return;
      }

      if (!looksLikeImageFile(file)) {
        resetFileSelection();
        showStatus("error", "Image required", "Please choose a screenshot image.");
        return;
      }

      state.previewUrl = URL.createObjectURL(file);

      if (elements.previewImage) {
        elements.previewImage.src = state.previewUrl;
      }

      if (elements.imagePreview) {
        elements.imagePreview.hidden = false;
      }

      try {
        const normalized = await normalizeImageToPng(file);
        state.preparedBlob = normalized.blob;
        state.preparedFileName = normalized.name;
        if (elements.uploadStatus) {
          elements.uploadStatus.textContent = normalized.name;
        }
      } catch (error) {
        state.preparedBlob = file;
        state.preparedFileName = file.name || "screenshot-upload";
      }

      updateStickyAction();
      extractInformation();
    }

    async function extractInformation() {
      if (!state.preparedBlob) {
        showStatus("error", "Screenshot required", "Select a screenshot before continuing.");
        updateStickyAction();
        return;
      }

      clearStatus();
      setStep(2);
      scrollToTopSmooth();

      const formData = new FormData();
      formData.append(
        "tech_number",
        elements.techNumberInitial ? elements.techNumberInitial.value.trim() : ""
      );
      formData.append(
        "supervisor",
        elements.supervisorInitial ? elements.supervisorInitial.value.trim() : ""
      );
      formData.append(
        "location",
        elements.locationInitial ? elements.locationInitial.value.trim() : ""
      );

      if (elements.keepScreenshot && elements.keepScreenshot.checked) {
        formData.append("keep_screenshot", "true");
      }

      formData.append(
        "screenshot",
        state.preparedBlob,
        state.preparedFileName || "epon-screenshot.png"
      );

      state.keepScreenshot = elements.keepScreenshot ? elements.keepScreenshot.checked : false;

      try {
        const payload = await fetchJson("/api/epon-additional-billing/extract", {
          method: "POST",
          headers: {
            "X-CSRF-Token": window.APP_CONFIG ? window.APP_CONFIG.csrfToken : "",
          },
          body: formData,
        });

        state.fields = payload.fields || {};
        state.confidence = payload.confidence || {};
        state.warnings = payload.warnings || [];
        state.screenshotFilename = payload.meta ? payload.meta.screenshot_filename : null;
        state.extractionJson = payload;

        populateReviewForm();
        renderWarnings();
        toggleBillingQuantityFields();
        showStatus("success", "Information extracted", "Review the EPON details below before submitting.");
        setStep(3);
        scrollToTopSmooth();
      } catch (error) {
        setStep(1);
        showStatus(
          "error",
          "Extraction could not be completed",
          error && error.message ? String(error.message) : "Unable to extract information from the screenshot."
        );
        scrollToTopSmooth();
      }
    }

    async function submitRequest() {
      if (!validateReviewForm()) return;

      syncReviewState();
      setStep(4);
      clearStatus();
      scrollToTopSmooth();

      try {
        const result = await fetchJson("/api/epon-additional-billing/submit", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": window.APP_CONFIG ? window.APP_CONFIG.csrfToken : "",
          },
          body: JSON.stringify({
            fields: state.fields,
            screenshot_filename: state.screenshotFilename,
            delete_screenshot_after: !state.keepScreenshot,
            extraction_json: state.extractionJson,
          }),
        });

        if (elements.openExternalButton && result.external_url) {
          elements.openExternalButton.href = result.external_url;
        }

        if (elements.successMessage) {
          elements.successMessage.textContent =
            result.message || "The EPON additional billing request was submitted successfully.";
        }

        setStep(5);
        showStatus("success", "Request sent", "The EPON request was submitted successfully.");
        scrollToTopSmooth();
      } catch (error) {
        setStep(3);
        showStatus(
          "error",
          "EPON submit failed",
          error && error.message ? String(error.message) : "Unable to submit the EPON request."
        );
        scrollToTopSmooth();
      }
    }

    function handleStickyAction() {
      if (state.step === 1) {
        extractInformation();
        return;
      }

      if (state.step === 3) {
        submitRequest();
        return;
      }

      if (state.step === 5 && elements.openExternalButton) {
        window.open(elements.openExternalButton.href, "_blank", "noopener,noreferrer");
      }
    }

    function validateReviewForm() {
      syncReviewState();

      let isValid = true;
      requiredFields.forEach((name) => {
        const input = reviewInputs[name];
        const hasValue = Boolean(input && input.value.trim());

        if (input) {
          input.classList.toggle("is-invalid", !hasValue);
        }

        isValid = isValid && hasValue;
      });

      if (state.fields.billing_type === "RR8") {
        const ok = Boolean(reviewInputs.rr8_quantity && reviewInputs.rr8_quantity.value.trim());
        if (reviewInputs.rr8_quantity) {
          reviewInputs.rr8_quantity.classList.toggle("is-invalid", !ok);
        }
        isValid = isValid && ok;
      }

      if (state.fields.billing_type === "RS3") {
        const ok = Boolean(reviewInputs.rs3_quantity && reviewInputs.rs3_quantity.value.trim());
        if (reviewInputs.rs3_quantity) {
          reviewInputs.rs3_quantity.classList.toggle("is-invalid", !ok);
        }
        isValid = isValid && ok;
      }

      if (!isValid) {
        showStatus("error", "Required fields are missing", "Complete all required EPON fields before submitting.");
        scrollToFirstInvalidField();
      } else {
        clearStatus();
      }

      updateStickyAction();
      return isValid;
    }

    function syncReviewState() {
      Object.entries(reviewInputs).forEach(([name, input]) => {
        state.fields[name] = input.value.trim();
      });
    }

    function populateReviewForm() {
      Object.entries(reviewInputs).forEach(([name, input]) => {
        const value = typeof state.fields[name] === "string" ? state.fields[name] : "";
        if (value) {
          input.value = value;
        }
        input.classList.remove("is-invalid");
      });

      document.querySelectorAll("[data-confidence-for]").forEach((node) => {
        const key = node.dataset.confidenceFor;
        const confidence = state.confidence[key];
        node.textContent =
          typeof confidence === "number" ? `Confidence ${Math.round(confidence * 100)}%` : "";
      });
    }

    function renderWarnings() {
      if (!elements.reviewWarnings) return;

      if (!state.warnings.length) {
        elements.reviewWarnings.hidden = true;
        elements.reviewWarnings.innerHTML = "";
        return;
      }

      elements.reviewWarnings.hidden = false;
      const items = state.warnings
        .map((warning) => `<li>${escapeHtml(warning)}</li>`)
        .join("");

      elements.reviewWarnings.innerHTML =
        `<div class="status-card is-warning"><strong>Review recommended</strong><ul>${items}</ul></div>`;
    }

    function toggleBillingQuantityFields() {
      const type = elements.billingType ? elements.billingType.value : "";

      if (elements.rr8QuantityField) {
        elements.rr8QuantityField.hidden = type !== "RR8";
      }
      if (elements.rs3QuantityField) {
        elements.rs3QuantityField.hidden = type !== "RS3";
      }

      if (elements.rr8Quantity) {
        if (type !== "RR8") elements.rr8Quantity.value = "";
      }
      if (elements.rs3Quantity) {
        if (type !== "RS3") elements.rs3Quantity.value = "";
      }
    }

    function restartFlow() {
      state.step = 1;
      state.fields = {};
      state.confidence = {};
      state.warnings = [];
      state.screenshotFilename = null;
      state.extractionJson = {};

      if (elements.extractForm) elements.extractForm.reset();
      if (elements.reviewForm) elements.reviewForm.reset();
      if (elements.uploadStatus) elements.uploadStatus.textContent = "No file selected";
      if (elements.dropzone) elements.dropzone.classList.remove("is-ready");
      if (elements.reviewWarnings) {
        elements.reviewWarnings.hidden = true;
        elements.reviewWarnings.innerHTML = "";
      }

      clearPreparedFile(false);
      clearStatus();
      syncInitialValues();
      toggleBillingQuantityFields();
      setStep(1);
      scrollToTopSmooth();
    }

    function setStep(step) {
      state.step = step;

      cardsByStep.forEach((node, nodeStep) => {
        node.hidden = nodeStep !== step;
      });

      progressSteps.forEach((node) => {
        const nodeStep = Number(node.dataset.progressStep);
        const current = progressStepFor(step);
        node.classList.toggle("is-active", nodeStep === current);
        node.classList.toggle("is-complete", nodeStep < current);
      });

      updateStickyAction();
    }

    function progressStepFor(step) {
      if (step <= 2) return 1;
      if (step === 3) return 3;
      if (step === 4) return 4;
      return 5;
    }

    function updateStickyAction() {
      if (!elements.stickyTitle || !elements.stickySubtitle || !elements.stickyButton) return;

      let title = "Upload Screenshot";
      let subtitle = "Select a screenshot to continue.";
      let label = "Extract Information";
      let disabled = false;

      if (state.step === 1) {
        subtitle = state.preparedBlob ? "Ready to extract EPON information." : "Select a screenshot to continue.";
        disabled = !state.preparedBlob;
      } else if (state.step === 2) {
        title = "Extracting Information";
        subtitle = "Please wait while the screenshot is processed.";
        label = "Processing";
        disabled = true;
      } else if (state.step === 3) {
        title = "Review Details";
        subtitle = "Confirm all required EPON fields before submitting.";
        label = "Submit EPON Request";
        disabled = !canSubmitReview();
      } else if (state.step === 4) {
        title = "Submitting Request";
        subtitle = "Sending the reviewed details now.";
        label = "Submitting";
        disabled = true;
      } else if (state.step === 5) {
        title = "Request Sent";
        subtitle = "You can open the CUI page or start a new EPON request.";
        label = "Open CUI Page";
        disabled = false;
      }

      elements.stickyTitle.textContent = title;
      elements.stickySubtitle.textContent = subtitle;
      elements.stickyButton.textContent = label;
      elements.stickyButton.disabled = disabled;
    }

    function canSubmitReview() {
      syncReviewState();
      const baseComplete = requiredFields.every(
        (name) => reviewInputs[name] && reviewInputs[name].value.trim()
      );

      if (!baseComplete) return false;

      if (state.fields.billing_type === "RR8") {
        return Boolean(reviewInputs.rr8_quantity && reviewInputs.rr8_quantity.value.trim());
      }

      if (state.fields.billing_type === "RS3") {
        return Boolean(reviewInputs.rs3_quantity && reviewInputs.rs3_quantity.value.trim());
      }

      return false;
    }

    function clearPreparedFile(clearInput = true) {
      state.preparedBlob = null;
      state.preparedFileName = "";

      if (state.previewUrl) {
        URL.revokeObjectURL(state.previewUrl);
        state.previewUrl = null;
      }

      if (elements.imagePreview) {
        elements.imagePreview.hidden = true;
      }

      if (elements.previewImage) {
        elements.previewImage.removeAttribute("src");
      }

      if (clearInput && elements.screenshotInput) {
        elements.screenshotInput.value = "";
      }
    }

    function resetFileSelection() {
      if (elements.uploadStatus) {
        elements.uploadStatus.textContent = "No file selected";
      }

      if (elements.dropzone) {
        elements.dropzone.classList.remove("is-ready");
      }

      clearPreparedFile(true);
      updateStickyAction();
    }

    function showStatus(type, title, message) {
      if (!elements.statusBanner) return;
      elements.statusBanner.innerHTML =
        `<div class="status-card is-${type}"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
    }

    function clearStatus() {
      if (elements.statusBanner) elements.statusBanner.innerHTML = "";
    }
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const text = await response.text();

    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        throw new Error("Server returned an invalid response. Refresh the page and try again.");
      }
    }

    if (!response.ok) {
      throw new Error(payload.error || "Request failed.");
    }

    return payload;
  }

  function loadPreference(key, fallback = "") {
    try {
      const value = window.localStorage.getItem(key);
      return value !== null ? value : fallback;
    } catch (error) {
      return fallback;
    }
  }

  function savePreference(key, value) {
    try {
      window.localStorage.setItem(key, value || "");
    } catch (error) {
      // ignore
    }
  }

  function removePreference(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (error) {
      // ignore
    }
  }

  function looksLikeImageFile(file) {
    const fileName = (file.name || "").toLowerCase();
    const fileType = (file.type || "").toLowerCase();

    if (fileType.startsWith("image/")) return true;

    return [".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"].some((ext) =>
      fileName.endsWith(ext)
    );
  }

  async function normalizeImageToPng(file) {
    const sourceUrl = URL.createObjectURL(file);

    try {
      const image = await loadImage(sourceUrl);
      const canvas = document.createElement("canvas");
      const width = image.naturalWidth || image.width;
      const height = image.naturalHeight || image.height;

      canvas.width = width;
      canvas.height = height;

      const context = canvas.getContext("2d");
      if (!context) throw new Error("Canvas unavailable.");

      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = "high";
      context.drawImage(image, 0, 0, width, height);

      const blob = await new Promise((resolve, reject) => {
        canvas.toBlob((result) => {
          if (result) resolve(result);
          else reject(new Error("PNG conversion failed."));
        }, "image/png");
      });

      return {
        blob,
        name: "screenshot-upload.png",
      };
    } finally {
      URL.revokeObjectURL(sourceUrl);
    }
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("Image could not be loaded."));
      image.src = url;
    });
  }

  function createSignaturePad(canvas) {
    if (!canvas) {
      return {
        clear() {},
        isEmpty() { return true; },
        toDataURL() { return ""; },
        resize() {},
        onChange() {},
      };
    }

    const context = canvas.getContext("2d");
    let drawing = false;
    let hasStroke = false;

    function resize() {
      const ratio = Math.max(window.devicePixelRatio || 1, 1);
      const width = Math.max((canvas.parentElement ? canvas.parentElement.clientWidth : 280) - 2, 280);
      const height = 220;
      const previous = hasStroke ? canvas.toDataURL() : null;

      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;

      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.lineWidth = 2.2;
      context.lineCap = "round";
      context.lineJoin = "round";
      context.strokeStyle = "#102033";
      context.clearRect(0, 0, width, height);

      if (previous) {
        const image = new Image();
        image.onload = () => context.drawImage(image, 0, 0, width, height);
        image.src = previous;
      }
    }

    function point(event) {
      const rect = canvas.getBoundingClientRect();
      return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      };
    }

    canvas.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      drawing = true;
      const current = point(event);
      context.beginPath();
      context.moveTo(current.x, current.y);
    });

    canvas.addEventListener("pointermove", (event) => {
      if (!drawing) return;
      event.preventDefault();
      const current = point(event);
      context.lineTo(current.x, current.y);
      context.stroke();
      hasStroke = true;
      pad.onChange();
    });

    ["pointerup", "pointerleave", "pointercancel"].forEach((name) => {
      canvas.addEventListener(name, (event) => {
        if (!drawing) return;
        event.preventDefault();
        drawing = false;
        context.closePath();
        pad.onChange();
      });
    });

    const pad = {
      clear() {
        hasStroke = false;
        context.clearRect(0, 0, canvas.width, canvas.height);
        pad.onChange();
      },
      isEmpty() {
        return !hasStroke;
      },
      toDataURL() {
        return canvas.toDataURL("image/png");
      },
      resize,
      onChange() {},
    };

    resize();
    return pad;
  }

  function scrollToTopSmooth() {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function scrollToFirstInvalidField() {
    const firstInvalid = document.querySelector(".is-invalid");
    if (firstInvalid) {
      firstInvalid.scrollIntoView({ behavior: "smooth", block: "center" });
      if (typeof firstInvalid.focus === "function") {
        firstInvalid.focus({ preventScroll: true });
      }
    } else {
      scrollToTopSmooth();
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
})();