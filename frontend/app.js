// ===== Переключение темы =====
const themeToggle = document.getElementById('themeToggle');

function initTheme() {
  const savedTheme = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);
}

if (themeToggle) {
  themeToggle.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
  });
}

initTheme();

// ===== Элементы DOM =====
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('fileInfo');
const fileName = document.getElementById('fileName');
const fileSize = document.getElementById('fileSize');
const projectField = document.getElementById('projectField');
const projectNameInput = document.getElementById('projectNameInput');
const processBtn = document.getElementById('processBtn');
const processBtnText = document.getElementById('processBtnText');
const processBtnIcon = document.getElementById('processBtnIcon');
const btnSpinner = document.getElementById('btnSpinner');
const resetBtn = document.getElementById('resetBtn');
const progressSection = document.getElementById('progressSection');
const progressBar = document.getElementById('progressBar');
const progressDone = document.getElementById('progressDone');
const progressDoneText = document.getElementById('progressDoneText');
const progressDoneClose = document.getElementById('progressDoneClose');
const recentFilesList = document.getElementById('recentFilesList');

let selectedFile = null;

// ===== Сохранение состояния АКТИВНОГО задания (переживает перезагрузку) =====
const PENDING_JOB_KEY = 'igmi_pending_job';

function saveJobState(jobId, projectName) {
  localStorage.setItem(PENDING_JOB_KEY, JSON.stringify({
    jobId,
    projectName,
    savedAt: Date.now(),
  }));
}

function loadJobState() {
  try {
    const raw = localStorage.getItem(PENDING_JOB_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    console.error('Не удалось прочитать состояние задания:', e);
    return null;
  }
}

function clearJobState() {
  localStorage.removeItem(PENDING_JOB_KEY);
}

// ===== Сохранение состояния ЗАВЕРШЁННОЙ обработки (плашка после перезагрузки) =====
const COMPLETED_JOB_KEY = 'igmi_completed_job';

function saveCompletedState(filename) {
  localStorage.setItem(COMPLETED_JOB_KEY, JSON.stringify({ filename }));
}

function loadCompletedState() {
  try {
    const raw = localStorage.getItem(COMPLETED_JOB_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function clearCompletedState() {
  localStorage.removeItem(COMPLETED_JOB_KEY);
}

// ===== Крестик на плашке завершения =====
if (progressDoneClose) {
  progressDoneClose.addEventListener('click', () => {
    progressDone.classList.remove('visible');
    clearCompletedState();
  });
}

// ===== Валидация формы =====
function updateProcessButtonState() {
  const hasFile = !!selectedFile;
  const hasProject = projectNameInput.value.trim().length > 0;
  processBtn.disabled = !(hasFile && hasProject);

  if (hasFile && !hasProject) {
    projectNameInput.classList.add('invalid');
  } else {
    projectNameInput.classList.remove('invalid');
  }
}

projectNameInput.addEventListener('input', updateProcessButtonState);

// ===== Drag & Drop и выбор файла =====
dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length > 0) {
    handleFile(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener('change', (e) => {
  if (e.target.files.length > 0) {
    handleFile(e.target.files[0]);
  }
});

function stripExtension(filename) {
  const idx = filename.lastIndexOf('.');
  return idx > 0 ? filename.slice(0, idx) : filename;
}

function handleFile(file) {
  if (!file.name.toLowerCase().endsWith('.docx')) {
    showToast('Пожалуйста, выберите файл .docx', true);
    return;
  }

  selectedFile = file;
  fileName.textContent = file.name;
  fileSize.textContent = formatFileSize(file.size);
  fileInfo.classList.add('visible');

  projectNameInput.value = stripExtension(file.name);
  projectField.classList.add('visible');

  resetBtn.style.display = 'inline-flex';
  updateProcessButtonState();
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// ===== Обработка =====
processBtn.addEventListener('click', startProcessing);

async function startProcessing() {
  if (!selectedFile) return;

  const projectName = (projectNameInput.value || '').trim();

  if (!projectName) {
    showToast('Укажите название проекта', true);
    projectNameInput.classList.add('invalid');
    projectNameInput.focus();
    return;
  }

  // Блокируем UI
  processBtn.disabled = true;
  resetBtn.disabled = true;
  btnSpinner.classList.add('spinning');
  processBtnIcon.style.display = 'none';
  processBtnText.textContent = 'Отправка...';

  progressSection.classList.add('visible');
  progressDone.classList.remove('visible');
  progressBar.classList.add('active');
  progressBar.classList.remove('done');

  const steps = document.querySelectorAll('.progress-step');
  steps.forEach((step, i) => {
    step.classList.remove('active', 'completed');
    step.querySelector('.progress-step__icon').textContent = i + 1;
    const textEl = step.querySelector('.progress-step__text');
    if (!textEl.dataset.default) {
      textEl.dataset.default = textEl.textContent;
    } else {
      textEl.textContent = textEl.dataset.default;
    }
  });

  // Отправка на сервер
  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('project', projectName);

  let jobId = null;
  try {
    const res = await fetch('/api/generate', { method: 'POST', body: formData });
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`HTTP ${res.status}: ${errText}`);
    }
    const data = await res.json();
    jobId = data.job_id;
    processBtnText.textContent = 'Обработка...';

    // Сохраняем, чтобы пережить перезагрузку страницы
    saveJobState(jobId, projectName);
  } catch (e) {
    showToast('Ошибка отправки: ' + e.message, true);
    resetUI();
    return;
  }

  try {
    await pollJob(jobId, steps);
  } catch (e) {
    showToast('Ошибка обработки: ' + e.message, true);
    resetUI();
  }
}

async function pollJob(jobId, steps) {
  const POLL_INTERVAL = 600;
  let lastStep = 0;

  while (true) {
    const res = await fetch(`/api/job/${jobId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const job = await res.json();

    // Обновляем визуализацию шагов
    if (job.step !== lastStep) {
      for (let i = 0; i < steps.length; i++) {
        const stepNum = i + 1;
        if (stepNum < job.step) {
          steps[i].classList.remove('active');
          steps[i].classList.add('completed');
          steps[i].querySelector('.progress-step__icon').textContent = '✓';
        } else if (stepNum === job.step && job.status === 'running') {
          steps[i].classList.add('active');
          steps[i].classList.remove('completed');
          if (job.step_name) {
            steps[i].querySelector('.progress-step__text').textContent = job.step_name;
          }
        }
      }
      lastStep = job.step;
    }

    if (job.status === 'queued') {
      processBtnText.textContent = `В очереди: ${job.queue_position + 1}`;
    }

    // Успешное завершение
    if (job.status === 'done') {
      steps.forEach(s => {
        s.classList.remove('active');
        s.classList.add('completed');
        s.querySelector('.progress-step__icon').textContent = '✓';
      });
      progressBar.classList.remove('active');
      progressBar.classList.add('done');

      progressDoneText.textContent =
        `Готово! Файл «${job.filename}» создан и добавлен в список ниже.`;
      progressDone.classList.add('visible');

      // Сохраняем, чтобы плашка пережила перезагрузку страницы
      saveCompletedState(job.filename);
      clearJobState();

      showToast('Документ «' + job.filename + '» готов!');

      await refreshFilesList();
      await sleep(1800);
      resetForm();   // сбрасываем форму, но плашку НЕ трогаем
      return;
    }

    if (job.status === 'error') {
      clearJobState();
      throw new Error(job.error || 'Неизвестная ошибка сервера');
    }

    await sleep(POLL_INTERVAL);
  }
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// Полный сброс UI (включая плашку завершения)
function resetUI() {
  selectedFile = null;
  clearJobState();
  clearCompletedState();

  fileInfo.classList.remove('visible');
  projectField.classList.remove('visible');
  progressSection.classList.remove('visible');
  progressBar.classList.remove('active', 'done');
  progressDone.classList.remove('visible');

  processBtn.disabled = true;
  resetBtn.disabled = false;
  resetBtn.style.display = 'none';
  btnSpinner.classList.remove('spinning');
  processBtnIcon.style.display = '';
  processBtnText.textContent = 'Начать обработку';

  const steps = document.querySelectorAll('.progress-step');
  steps.forEach((step, i) => {
    step.classList.remove('active', 'completed');
    step.querySelector('.progress-step__icon').textContent = i + 1;
    const textEl = step.querySelector('.progress-step__text');
    if (textEl.dataset.default) {
      textEl.textContent = textEl.dataset.default;
    }
  });

  fileInput.value = '';
  projectNameInput.classList.remove('invalid');
  updateProcessButtonState();
}

// Сброс формы БЕЗ скрытия плашки завершения
function resetForm() {
  selectedFile = null;
  clearJobState();

  fileInfo.classList.remove('visible');
  projectField.classList.remove('visible');
  progressSection.classList.remove('visible');
  progressBar.classList.remove('active', 'done');

  processBtn.disabled = true;
  resetBtn.disabled = false;
  resetBtn.style.display = 'none';
  btnSpinner.classList.remove('spinning');
  processBtnIcon.style.display = '';
  processBtnText.textContent = 'Начать обработку';

  const steps = document.querySelectorAll('.progress-step');
  steps.forEach((step, i) => {
    step.classList.remove('active', 'completed');
    step.querySelector('.progress-step__icon').textContent = i + 1;
    const textEl = step.querySelector('.progress-step__text');
    if (textEl.dataset.default) {
      textEl.textContent = textEl.dataset.default;
    }
  });

  fileInput.value = '';
  projectNameInput.classList.remove('invalid');
  updateProcessButtonState();
}

resetBtn.addEventListener('click', resetUI);

// ===== Toast-уведомления =====
const toast = document.getElementById('toast');
const toastIcon = document.getElementById('toastIcon');
const toastText = document.getElementById('toastText');

function showToast(message, isError = false, icon = null) {
  if (!toast || !toastText) return;
  if (toastIcon) toastIcon.textContent = icon || (isError ? '❌' : '✅');
  toastText.textContent = message;
  toast.style.background = isError ? 'var(--danger)' : 'var(--success)';
  toast.classList.add('visible');
  setTimeout(() => toast.classList.remove('visible'), 3500);
}

// ===== Список готовых файлов =====
const collapsedProjects = new Set();

async function refreshFilesList() {
  try {
    const res = await fetch('/api/files');
    if (!res.ok) return;
    const groups = await res.json();
    renderGroups(groups);
  } catch (e) {
    console.error('Не удалось загрузить файлы:', e);
  }
}

function renderGroups(groups) {
  if (!groups || groups.length === 0) {
    recentFilesList.innerHTML =
      '<div class="empty-state"><div style="font-size: 2rem; margin-bottom: 0.5rem;">📭</div>' +
      '<div>Пока нет обработанных файлов</div></div>';
    return;
  }

  recentFilesList.innerHTML = groups.map(group => {
    const isCollapsed = collapsedProjects.has(group.project);
    const filesHtml = group.files.map(file =>
      '<div class="file-item">' +
        '<div class="file-item__info">' +
          '<div class="file-item__name">' + escapeHtml(file.name) + '</div>' +
          '<div class="file-item__date">' + formatDate(file.date) +
            (file.size ? ' · ' + formatFileSize(file.size) : '') +
          '</div>' +
        '</div>' +
        '<a class="file-item__download" href="/api/download/' +
          encodeURIComponent(file.rel_path) + '" download>⬇️</a>' +
      '</div>'
    ).join('');

    return (
      '<div class="file-group' + (isCollapsed ? ' file-group--collapsed' : '') + '">' +
        '<button class="file-group__header" data-project="' + escapeHtml(group.project) + '">' +
          '<span class="file-group__chevron">▾</span>' +
          '<span class="file-group__name">' + escapeHtml(group.project) + '</span>' +
          '<span class="file-group__count">' + group.files.length + '</span>' +
        '</button>' +
        '<div class="file-group__files">' + filesHtml + '</div>' +
      '</div>'
    );
  }).join('');
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

recentFilesList.addEventListener('click', (e) => {
  const header = e.target.closest('.file-group__header');
  if (header) {
    const project = header.dataset.project;
    if (collapsedProjects.has(project)) {
      collapsedProjects.delete(project);
    } else {
      collapsedProjects.add(project);
    }
    refreshFilesList();
  }
});

function formatDate(isoString) {
  const date = new Date(isoString);
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ===== Возобновление незавершённого задания после перезагрузки =====
async function resumePendingJob() {
  const state = loadJobState();
  if (!state || !state.jobId) return;

  let job = null;
  try {
    const res = await fetch(`/api/job/${state.jobId}`);
    if (!res.ok) {
      // Сервер был перезапущен — задание потеряно (тихо очищаем)
      clearJobState();
      return;
    }
    job = await res.json();
  } catch (e) {
    console.error('Ошибка проверки задания:', e);
    clearJobState();
    return;
  }

  // Если уже завершено или упало — очищаем
  if (job.status === 'done' || job.status === 'error') {
    clearJobState();
    if (job.status === 'done') {
      await refreshFilesList();
    }
    return;
  }

  // Задание ещё в работе — восстанавливаем UI и продолжаем polling (без уведомлений)
  processBtn.disabled = true;
  resetBtn.disabled = true;
  resetBtn.style.display = 'inline-flex';
  btnSpinner.classList.add('spinning');
  processBtnIcon.style.display = 'none';
  processBtnText.textContent = 'Обработка...';

  progressSection.classList.add('visible');
  progressDone.classList.remove('visible');
  progressBar.classList.add('active');
  progressBar.classList.remove('done');

  const steps = document.querySelectorAll('.progress-step');
  steps.forEach((step, i) => {
    step.classList.remove('active', 'completed');
    step.querySelector('.progress-step__icon').textContent = i + 1;
    const textEl = step.querySelector('.progress-step__text');
    if (!textEl.dataset.default) {
      textEl.dataset.default = textEl.textContent;
    }
  });

  try {
    await pollJob(state.jobId, steps);
  } catch (e) {
    showToast('Ошибка обработки: ' + e.message, true);
    resetUI();
  }
}

// ===== Инициализация =====
refreshFilesList();
setInterval(refreshFilesList, 30000);

// Показываем плашку завершённой обработки, если пользователь её ещё не закрыл
const completedState = loadCompletedState();
if (completedState && completedState.filename) {
  progressDoneText.textContent =
    `Готово! Файл «${completedState.filename}» создан и добавлен в список ниже.`;
  progressDone.classList.add('visible');
}

// Пытаемся возобновить незавершённое задание после перезагрузки
resumePendingJob().catch(err => {
  console.error('Не удалось возобновить задание:', err);
  clearJobState();
});