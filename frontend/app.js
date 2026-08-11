// ===== Тема =====
function initTheme() {
  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.body.setAttribute('data-theme', savedTheme);
}
initTheme();

const themeToggle = document.getElementById('themeToggle');

function initTheme() {
  const saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
}

if (themeToggle) {
  themeToggle.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'light' ? 'dark' : 'dark' === current ? 'light' : 'light';
    const newTheme = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
  });
}

initTheme();
// ===== Пользователь =====
// Идентификатор пользователя — для статистики. Хранится в localStorage,
// можно задать через prompt при первом входе или оставить анонимным.
function getUser() {
  let u = localStorage.getItem('user');
  if (!u) {
    // При первом заходе спрашиваем имя (один раз)
    const name = prompt(
      'Как к вам обращаться? (для статистики использования)\n' +
      'Можно оставить пустым — будет "аноним".'
    );
    u = (name || '').trim() || 'anonymous';
    localStorage.setItem('user', u);
  }
  return u;
}
const CURRENT_USER = getUser();

// ===== File Upload =====
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

let selectedFile = null;

dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length > 0) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', (e) => {
  if (e.target.files.length > 0) handleFile(e.target.files[0]);
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
  processBtn.disabled = false;
  resetBtn.style.display = 'inline-flex';
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024, sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// ===== Process =====
processBtn.addEventListener('click', startProcessing);

async function startProcessing() {
  if (!selectedFile) return;

  const projectName = (projectNameInput.value || '').trim() || stripExtension(selectedFile.name);

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
    const defText = step.querySelector('.progress-step__text').dataset.default ||
                    step.querySelector('.progress-step__text').textContent;
    step.querySelector('.progress-step__text').dataset.default = defText;
    step.querySelector('.progress-step__text').textContent = defText;
  });

  // === Шаг 1: загрузка файла на сервер ===
  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('project', projectName);
  formData.append('user', CURRENT_USER);

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
  } catch (e) {
    showToast('Ошибка отправки: ' + e.message, true);
    resetUI();
    return;
  }

  // === Шаг 2: polling статуса задания ===
  try {
    await pollJob(jobId, steps);
  } catch (e) {
    showToast('Ошибка обработки: ' + e.message, true);
    resetUI();
    return;
  }
}

async function pollJob(jobId, steps) {
  const POLL_INTERVAL = 600;
  let lastStep = 0;

  while (true) {
    const res = await fetch(`/api/job/${jobId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const job = await res.json();

    // Отображаем текущий шаг
    if (job.step !== lastStep) {
      // Все предыдущие шаги — completed
      for (let i = 0; i < steps.length; i++) {
        const stepNum = i + 1;
        if (stepNum < job.step) {
          steps[i].classList.remove('active');
          steps[i].classList.add('completed');
          steps[i].querySelector('.progress-step__icon').textContent = '✓';
        } else if (stepNum === job.step && job.status === 'running') {
          steps[i].classList.add('active');
          steps[i].classList.remove('completed');
          // Показываем актуальное имя шага с сервера
          if (job.step_name) {
            steps[i].querySelector('.progress-step__text').textContent = job.step_name;
          }
        }
      }
      lastStep = job.step;
    }

    // Очередь (если ещё не запущено)
    if (job.status === 'queued') {
      processBtnText.textContent = `В очереди: ${job.queue_position + 1}`;
    }

    // Финальные состояния
    if (job.status === 'done') {
      // Все шаги — completed
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
      showToast('Документ «' + job.filename + '» готов!');

      await refreshFilesList();
      await sleep(1800);
      resetUI();
      return;
    }

    if (job.status === 'error') {
      throw new Error(job.error || 'Неизвестная ошибка сервера');
    }

    await sleep(POLL_INTERVAL);
  }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function resetUI() {
  selectedFile = null;
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
    const def = step.querySelector('.progress-step__text').dataset.default;
    if (def) step.querySelector('.progress-step__text').textContent = def;
  });

  fileInput.value = '';
}

resetBtn.addEventListener('click', resetUI);

// ===== Toast =====
const toast = document.getElementById('toast');
const toastIcon = document.getElementById('toastIcon');
const toastText = document.getElementById('toastText');

function showToast(message, isError = false, icon = null) {
  toastIcon.textContent = icon || (isError ? '❌' : '✅');
  toastText.textContent = message;
  toast.style.background = isError ? 'var(--accent)' : 'var(--success)';
  toast.classList.add('visible');
  setTimeout(() => toast.classList.remove('visible'), 3500);
}

// ===== Recent Files =====
const recentFilesList = document.getElementById('recentFilesList');
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
          encodeURIComponent(file.name) + '" download>⬇️</a>' +
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
    if (collapsedProjects.has(project)) collapsedProjects.delete(project);
    else collapsedProjects.add(project);
    refreshFilesList();
  }
});

function formatDate(isoString) {
  const date = new Date(isoString);
  return date.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

// ===== Init =====
refreshFilesList();
// Обновляем список каждые 30 сек (если в другой вкладке что-то сгенерировалось)
setInterval(refreshFilesList, 30000);