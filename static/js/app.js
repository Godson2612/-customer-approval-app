(function () {
  const workflowScreen = document.querySelector('.workflow-screen');
  if (!workflowScreen) return;

  const isIPhone = /iPhone|iPod/i.test(navigator.userAgent || '');
  const isTelegramBrowser = /Telegram/i.test(navigator.userAgent || '');
  const isIPhoneTelegram = isIPhone && isTelegramBrowser;

  const state = {
    step: 1,
    fields: {},
    confidence: {},
    warnings: [],
    screenshotFilename: null,
    extractionJson: {},
    keepScreenshot: false,
    downloadUrl: '',
    shareMeta: null,
    signatures: { customer: null, technician: null },
    preparedFile: null,
    preparedFileName: '',
  };

  const elements = {
    statusBanner: document.getElementById('statusBanner'),
    extractForm: document.getElementById('extractForm'),
    reviewForm: document.getElementById('reviewForm'),
    screenshotInput: document.getElementById('screenshotInput'),
    imagePreview: document.getElementById('imagePreview'),
    previewImage: document.getElementById('previewImage'),
    uploadStatus: document.getElementById('uploadStatus'),
    dropzone: document.querySelector('.upload-dropzone'),
    reviewWarnings: document.getElementById('reviewWarnings'),
    stickyButton: document.getElementById('stickyPrimaryButton'),
    stickyTitle: document.getElementById('stickyTitle'),
    stickySubtitle: document.getElementById('stickySubtitle'),
    keepScreenshot: document.getElementById('keepScreenshot'),
    shareButton: document.getElementById('shareButton'),
    restartButton: document.getElementById('restartButton'),
    downloadButton: document.getElementById('downloadButton'),
    successMessage: document.getElementById('successMessage'),
    iosUploadHint: document.getElementById('iosUploadHint'),
    openExternalButton: document.getElementById('openExternalButton'),
  };

  let previewObjectUrl = null;

  const cardsByStep = new Map(
    Array.from(document.querySelectorAll('[data-step]')).map((node) => [Number(node.dataset.step), node])
  );

  const progressSteps = Array.from(document.querySelectorAll('[data-progress-step]'));

  const reviewFields = [
    'job_number',
    'customer_name',
    'service_address',
    'city_state_zip',
    'phone_number',
    'work_phone_number',
    'email',
    'installation_date',
    'technician_name',
  ];

  const requiredFields = [
    'job_number',
    'customer_name',
    'service_address',
    'city_state_zip',
    'phone_number',
    'installation_date',
    'technician_name',
  ];

  const reviewInputs = Object.fromEntries(
    reviewFields
      .map((name) => [name, document.querySelector(`[name="${name}"]`)])
      .filter(([, input]) => input)
  );

  const installationDateMirror = document.getElementById('installationDateDuplicate');
  const initialTechnicianInput = document.getElementById('technicianNameInitial');
  const customerPad = createSignaturePad(document.getElementById('customerSignaturePad'));
  const technicianPad = createSignaturePad(document.getElementById('technicianSignaturePad'));

  setupUploadHint();
  bindEvents();
  syncInitialValues();
  setStep(1);

  function setupUploadHint() {
    if (elements.iosUploadHint) {
      elements.iosUploadHint.hidden = !isIPhoneTelegram;
    }
    if (elements.openExternalButton) {
      elements.openExternalButton.href = window.location.href;
    }
  }

  function bindEvents() {
    if (elements.screenshotInput) {
      elements.screenshotInput.addEventListener('change', handleFileSelection);
    }

    if (elements.extractForm) {
      elements.extractForm.addEventListener('submit', (event) => {
        event.preventDefault();
        extractInformation();
      });
    }

    if (elements.reviewForm) {
      elements.reviewForm.addEventListener('submit', (event) => {
        event.preventDefault();
        continueFromReview();
      });
    }

    Object.values(reviewInputs).forEach((input) => {
      input.addEventListener('input', () => {
        if (input.name === 'technician_name' && initialTechnicianInput) {
          initialTechnicianInput.value = input.value;
        }
        if (input.name === 'installation_date' && installationDateMirror) {
          installationDateMirror.value = input.value;
        }
        input.classList.remove('is-invalid');
        updateStickyAction();
      });
    });

    if (installationDateMirror && reviewInputs.installation_date) {
      installationDateMirror.addEventListener('input', () => {
        reviewInputs.installation_date.value = installationDateMirror.value;
        reviewInputs.installation_date.dispatchEvent(new Event('input', { bubbles: true }));
      });
    }

    if (elements.keepScreenshot) {
      elements.keepScreenshot.addEventListener('change', () => {
        state.keepScreenshot = elements.keepScreenshot.checked;
      });
    }

    const clearCustomerButton = document.getElementById('clearCustomerSignature');
    if (clearCustomerButton) {
      clearCustomerButton.addEventListener('click', () => {
        customerPad.clear();
        state.signatures.customer = null;
        updateStickyAction();
      });
    }

    const clearTechnicianButton = document.getElementById('clearTechnicianSignature');
    if (clearTechnicianButton) {
      clearTechnicianButton.addEventListener('click', () => {
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
      elements.stickyButton.addEventListener('click', handleStickyAction);
    }

    if (elements.shareButton) {
      elements.shareButton.addEventListener('click', shareDocument);
    }

    if (elements.restartButton) {
      elements.restartButton.addEventListener('click', restartFlow);
    }

    window.addEventListener('resize', () => {
      customerPad.resize();
      technicianPad.resize();
    });
  }

  function syncInitialValues() {
    const defaultTech = workflowScreen.dataset.defaultTech || '';
    const today = workflowScreen.dataset.today || '';

    if (reviewInputs.technician_name && !reviewInputs.technician_name.value) {
      reviewInputs.technician_name.value = defaultTech;
    }
    if (reviewInputs.installation_date && !reviewInputs.installation_date.value) {
      reviewInputs.installation_date.value = today;
    }
    if (installationDateMirror) {
      installationDateMirror.value = reviewInputs.installation_date ? reviewInputs.installation_date.value || today : today;
    }
    if (initialTechnicianInput && !initialTechnicianInput.value) {
      initialTechnicianInput.value = defaultTech;
    }
  }

  async function handleFileSelection() {
    const file = elements.screenshotInput ? elements.screenshotInput.files[0] : null;

    if (elements.uploadStatus) {
      elements.uploadStatus.textContent = file ? file.name : 'No file selected';
    }
    if (elements.dropzone) {
      elements.dropzone.classList.toggle('is-ready', Boolean(file));
    }

    if (previewObjectUrl) {
      URL.revokeObjectURL(previewObjectUrl);
      previewObjectUrl = null;
    }

    state.preparedFile = null;
    state.preparedFileName = '';

    if (!file) {
      if (elements.imagePreview) elements.imagePreview.hidden = true;
      if (elements.previewImage) elements.previewImage.removeAttribute('src');
      updateStickyAction();
      return;
    }

    const fileName = (file.name || '').toLowerCase();
    const allowed = fileName.endsWith('.png') || fileName.endsWith('.jpg') || fileName.endsWith('.jpeg');

    if (!allowed) {
      resetFileSelection();
      showStatus('error', 'Screenshot required', 'Please choose a PNG or JPG screenshot from Photos or Files.');
      return;
    }

    previewObjectUrl = URL.createObjectURL(file);
    if (elements.previewImage) elements.previewImage.src = previewObjectUrl;
    if (elements.imagePreview) elements.imagePreview.hidden = false;

    try {
      const prepared = await normalizeImageFile(file);
      state.preparedFile = prepared;
      state.preparedFileName = prepared.name;
      if (elements.uploadStatus) elements.uploadStatus.textContent = prepared.name;
    } catch (error) {
      state.preparedFile = file;
      state.preparedFileName = file.name || 'screenshot-upload.png';
    }

    updateStickyAction();

    if (isIPhoneTelegram) {
      showStatus(
        'warning',
        'Telegram on iPhone',
        'Choose the screenshot, then tap Extract Information. If Telegram still blocks it, open this page in Safari.'
      );
      return;
    }

    extractInformation();
  }

  async function normalizeImageFile(file) {
    const imageUrl = URL.createObjectURL(file);
    try {
      const image = await loadImage(imageUrl);
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth || image.width;
      canvas.height = image.naturalHeight || image.height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

      const blob = await new Promise((resolve, reject) => {
        canvas.toBlob((result) => {
          if (result) resolve(result);
          else reject(new Error('Image normalization failed.'));
        }, 'image/png');
      });

      return new File([blob], 'screenshot-upload.png', { type: 'image/png' });
    } finally {
      URL.revokeObjectURL(imageUrl);
    }
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('Image could not be loaded.'));
      image.src = url;
    });
  }

  function resetFileSelection() {
    if (elements.screenshotInput) elements.screenshotInput.value = '';
    if (elements.uploadStatus) elements.uploadStatus.textContent = 'No file selected';
    if (elements.dropzone) elements.dropzone.classList.remove('is-ready');
    if (elements.imagePreview) elements.imagePreview.hidden = true;
    if (elements.previewImage) elements.previewImage.removeAttribute('src');
    state.preparedFile = null;
    state.preparedFileName = '';
    updateStickyAction();
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
        showStatus('error', 'Customer signature required', 'Please capture the customer signature to continue.');
        return;
      }
      setStep(5);
      scrollToTopSmooth();
      return;
    }
    if (state.step === 5) {
      if (!state.signatures.technician) {
        showStatus('error', 'Technician signature required', 'Please capture the technician signature to continue.');
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
    if (!state.preparedFile) {
      showStatus('error', 'Screenshot required', 'Select a screenshot before continuing.');
      updateStickyAction();
      return;
    }

    clearStatus();
    setStep(2);
    scrollToTopSmooth();

    const formData = new FormData();
    formData.append('technician_name', initialTechnicianInput ? initialTechnicianInput.value.trim() : '');
    if (elements.keepScreenshot && elements.keepScreenshot.checked) {
      formData.append('keep_screenshot', 'true');
    }
    formData.append('screenshot', state.preparedFile, state.preparedFileName || 'screenshot-upload.png');
    state.keepScreenshot = elements.keepScreenshot ? elements.keepScreenshot.checked : false;

    try {
      const response = await fetch('/api/customer-approval/extract', {
        method: 'POST',
        headers: {
          'X-CSRF-Token': window.APP_CONFIG ? window.APP_CONFIG.csrfToken : '',
        },
        body: formData,
      });

      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || 'Unable to extract information.');
      }

      state.fields = payload.fields || {};
      state.confidence = payload.confidence || {};
      state.warnings = payload.warnings || [];
      state.screenshotFilename = payload.meta ? payload.meta.screenshot_filename : null;
      state.extractionJson = payload;

      populateReviewForm();
      renderWarnings();
      showStatus('success', 'Information extracted', 'Review the details below before collecting signatures.');
      setStep(3);
      scrollToTopSmooth();
    } catch (error) {
      setStep(1);
      const rawMessage = error && error.message ? String(error.message) : 'Unable to extract information.';
      if (rawMessage.includes('expected pattern') || rawMessage.includes('Load failed') || rawMessage.includes('could not be processed')) {
        showStatus(
          'error',
          'Extraction could not be completed',
          isIPhoneTelegram
            ? 'Telegram on iPhone is blocking the upload. Tap Open in Safari and choose the screenshot there.'
            : 'The selected file could not be processed. Please choose a PNG or JPG screenshot and try again.'
        );
      } else {
        showStatus('error', 'Extraction could not be completed', rawMessage);
      }
      scrollToTopSmooth();
    }
  }

  function populateReviewForm() {
    Object.entries(reviewInputs).forEach(([name, input]) => {
      const value = typeof state.fields[name] === 'string' ? state.fields[name] : '';
      if (value) input.value = value;
      if (name === 'technician_name' && !input.value && initialTechnicianInput) {
        input.value = initialTechnicianInput.value.trim();
      }
      input.classList.remove('is-invalid');
    });

    if (installationDateMirror && reviewInputs.installation_date) {
      installationDateMirror.value = reviewInputs.installation_date.value;
    }

    document.querySelectorAll('[data-confidence-for]').forEach((node) => {
      const key = node.dataset.confidenceFor;
      const confidence = state.confidence[key];
      node.textContent = typeof confidence === 'number' ? `Confidence ${Math.round(confidence * 100)}%` : '';
    });
  }

  function renderWarnings() {
    if (!elements.reviewWarnings) return;
    if (!state.warnings.length) {
      elements.reviewWarnings.hidden = true;
      elements.reviewWarnings.innerHTML = '';
      return;
    }
    elements.reviewWarnings.hidden = false;
    const items = state.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('');
    elements.reviewWarnings.innerHTML = `<div class="status-card is-warning"><strong>Review recommended</strong><ul>${items}</ul></div>`;
  }

  function validateReviewForm() {
    syncReviewState();
    let isValid = true;

    requiredFields.forEach((name) => {
      const input = reviewInputs[name];
      const hasValue = Boolean(input && input.value.trim());
      if (input) input.classList.toggle('is-invalid', !hasValue);
      isValid = isValid && hasValue;
    });

    if (!isValid) {
      showStatus('error', 'Required fields are missing', 'Complete all required fields before continuing.');
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
        customer_signature: state.signatures.customer || '',
        technician_signature: state.signatures.technician || '',
      },
      screenshot_filename: state.screenshotFilename,
      delete_screenshot_after: !state.keepScreenshot,
      extraction_json: state.extractionJson,
    };

    try {
      const response = await fetch('/api/customer-approval/generate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': window.APP_CONFIG ? window.APP_CONFIG.csrfToken : '',
        },
        body: JSON.stringify(payload),
      });

      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || 'Unable to generate the document.');
      }

      state.downloadUrl = result.download_url;
      state.shareMeta = result.share || null;
      if (elements.downloadButton) elements.downloadButton.href = result.download_url;
      if (elements.successMessage) {
        elements.successMessage.textContent = result.message || 'The approval PDF is ready for download or sharing.';
      }

      setStep(7);
      showStatus('success', 'Document ready', 'The approval PDF has been generated successfully.');
      scrollToTopSmooth();
    } catch (error) {
      setStep(5);
      showStatus('error', 'Document generation failed', error.message || 'Unable to generate document.');
      scrollToTopSmooth();
    }
  }

  async function shareDocument() {
    if (!state.downloadUrl) return;

    if (navigator.share) {
      try {
        await navigator.share({
          title: state.shareMeta && state.shareMeta.title ? state.shareMeta.title : 'Customer Approval',
          text: 'Customer approval document is ready.',
          url: `${window.location.origin}${state.downloadUrl}`,
        });
        return;
      } catch (error) {
        if (error && error.name === 'AbortError') return;
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
    state.downloadUrl = '';
    state.shareMeta = null;
    state.signatures.customer = null;
    state.signatures.technician = null;
    state.preparedFile = null;
    state.preparedFileName = '';

    if (elements.extractForm) elements.extractForm.reset();
    if (elements.reviewForm) elements.reviewForm.reset();
    if (elements.uploadStatus) elements.uploadStatus.textContent = 'No file selected';
    if (elements.dropzone) elements.dropzone.classList.remove('is-ready');
    if (elements.imagePreview) elements.imagePreview.hidden = true;
    if (elements.previewImage) elements.previewImage.removeAttribute('src');
    if (elements.reviewWarnings) {
      elements.reviewWarnings.hidden = true;
      elements.reviewWarnings.innerHTML = '';
    }
    if (previewObjectUrl) {
      URL.revokeObjectURL(previewObjectUrl);
      previewObjectUrl = null;
    }

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
      node.classList.toggle('is-active', nodeStep === current);
      node.classList.toggle('is-complete', nodeStep < current);
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

    let title = 'Upload Screenshot';
    let subtitle = 'Select a screenshot to continue.';
    let label = 'Extract Information';
    let disabled = false;

    if (state.step === 1) {
      subtitle = state.preparedFile ? 'Ready to extract information.' : 'Select a screenshot to continue.';
      disabled = !state.preparedFile;
    } else if (state.step === 2) {
      title = 'Extracting Information';
      subtitle = 'Please wait while the screenshot is processed.';
      label = 'Processing';
      disabled = true;
    } else if (state.step === 3) {
      title = 'Review Details';
      subtitle = 'Confirm all required fields before continuing.';
      label = 'Continue';
      disabled = !requiredFields.every((name) => reviewInputs[name] && reviewInputs[name].value.trim());
    } else if (state.step === 4) {
      title = 'Customer Signature';
      subtitle = state.signatures.customer ? 'Signature captured and ready to continue.' : 'Customer signature is required.';
      label = 'Continue';
      disabled = !state.signatures.customer;
    } else if (state.step === 5) {
      title = 'Technician Signature';
      subtitle = state.signatures.technician ? 'Ready to generate the final document.' : 'Technician signature is required.';
      label = 'Generate Document';
      disabled = !state.signatures.technician;
    } else if (state.step === 6) {
      title = 'Generating Document';
      subtitle = 'Finalizing the approval PDF.';
      label = 'Generating';
      disabled = true;
    } else if (state.step === 7) {
      title = 'Document Ready';
      subtitle = 'Download the approval PDF or start a new approval.';
      label = 'Download PDF';
      disabled = !state.downloadUrl;
    }

    elements.stickyTitle.textContent = title;
    elements.stickySubtitle.textContent = subtitle;
    elements.stickyButton.textContent = label;
    elements.stickyButton.disabled = disabled;
  }

  function scrollToTopSmooth() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function scrollToFirstInvalidField() {
    const firstInvalid = document.querySelector('.is-invalid');
    if (firstInvalid) {
      firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (typeof firstInvalid.focus === 'function') {
        firstInvalid.focus({ preventScroll: true });
      }
    } else {
      scrollToTopSmooth();
    }
  }

  function showStatus(type, title, message) {
    if (!elements.statusBanner) return;
    elements.statusBanner.innerHTML = `<div class="status-card is-${type}"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
  }

  function clearStatus() {
    if (elements.statusBanner) elements.statusBanner.innerHTML = '';
  }

  function createSignaturePad(canvas) {
    if (!canvas) {
      return {
        clear() {},
        isEmpty() { return true; },
        toDataURL() { return ''; },
        resize() {},
        onChange() {},
      };
    }

    const context = canvas.getContext('2d');
    let drawing = false;
    let hasStroke = false;

    function resize() {
      const ratio = Math.max(window.devicePixelRatio || 1, 1);
      const width = Math.max(canvas.parentElement.clientWidth - 2, 280);
      const height = 220;
      const previous = hasStroke ? canvas.toDataURL() : null;

      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;

      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.lineWidth = 2.2;
      context.lineCap = 'round';
      context.lineJoin = 'round';
      context.strokeStyle = '#102033';
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

    canvas.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      drawing = true;
      const current = point(event);
      context.beginPath();
      context.moveTo(current.x, current.y);
    });

    canvas.addEventListener('pointermove', (event) => {
      if (!drawing) return;
      event.preventDefault();
      const current = point(event);
      context.lineTo(current.x, current.y);
      context.stroke();
      hasStroke = true;
      pad.onChange();
    });

    ['pointerup', 'pointerleave', 'pointercancel'].forEach((name) => {
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
        return canvas.toDataURL('image/png');
      },
      resize,
      onChange() {},
    };

    resize();
    return pad;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }
})();