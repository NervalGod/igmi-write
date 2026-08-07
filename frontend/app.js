// Theme Management
const themeToggle = document.getElementById('themeToggle');
const themeIcon = document.getElementById('themeIcon');

function initTheme() {
  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.body.setAttribute('data-theme', savedTheme);
  updateThemeIcon(savedTheme);
}

function updateThemeIcon(theme) {
  themeIcon.textContent = theme === 'dark' ? '🌙' : '☀️';
}

themeToggle.addEventListener('click', () => {
  const currentTheme = document.body.getAttribute('data-theme');
  const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
  document.body.setAttribute('data-theme', newTheme);
  localStorage.setItem('theme', newTheme);
  updateThemeIcon(newTheme);
});

// File Upload
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('fileInfo');
const fileName = document.getElementById('fileName');
const fileSize = document.getElementById('fileSize');
const processBtn = document.getElementById('processBtn');
const resetBtn = document.getElementById('resetBtn');
const progressSection = document.getElementById('progressSection');

let selectedFile = null;

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
  const files = e.dataTransfer.files;
  if (files.length > 0) {
    handleFile(files[0]);
  }
});

fileInput.addEventListener('change', (e) => {
  if (e.target.files.length > 0) {
    handleFile(e.target.files[0]);
  }
});

function handleFile(file) {
  if (!file.name.endsWith('.docx')) {
    showToast('❌ Пожалуйста, выберите файл .docx', true);
    return;
  }
  
  selectedFile = file;
  fileName.textContent = file.name;
  fileSize.textContent = formatFileSize(file.size);
  fileInfo.classList.add('visible');
  processBtn.disabled = false;
  resetBtn.style.display = 'inline-flex';
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// Process Button
processBtn.addEventListener('click', startProcessing);

async function startProcessing() {
  if (!selectedFile) return;
  
  processBtn.disabled = true;
  progressSection.classList.add('visible');
  
  const steps = document.querySelectorAll('.progress-step');
  
  // Simulate processing steps
  for (let i = 0; i < steps.length; i++) {
    steps[i].classList.add('active');
    
    // Simulate work
    await new Promise(resolve => setTimeout(resolve, 1500 + Math.random() * 1000));
    
    steps[i].classList.remove('active');
    steps[i].classList.add('completed');
    
    // Update icon
    steps[i].querySelector('.progress-step__icon').textContent = '✓';
  }
  
  // Generate output filename
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const baseName = selectedFile.name.replace('.docx', '');
  const outputName = baseName + '_' + timestamp + '.docx';
  
  // Simulate file generation (in real app, this would call your backend)
  await simulateFileGeneration(outputName);
  
  // Add to recent files
  addRecentFile(outputName);
  
  // Show success toast
  showToast('✅ Документ успешно создан!');
  
  // Reset UI
  resetUI();
}

function simulateFileGeneration(outputName) {
  return new Promise(resolve => {
    setTimeout(() => {
      console.log('Generated file:', outputName);
      resolve();
    }, 1000);
  });
}

function resetUI() {
  selectedFile = null;
  fileInfo.classList.remove('visible');
  progressSection.classList.remove('visible');
  processBtn.disabled = true;
  resetBtn.style.display = 'none';
  
  const steps = document.querySelectorAll('.progress-step');
  steps.forEach((step, index) => {
    step.classList.remove('active', 'completed');
    step.querySelector('.progress-step__icon').textContent = index + 1;
  });
}

resetBtn.addEventListener('click', resetUI);

// Toast Notification
const toast = document.getElementById('toast');
const toastText = document.getElementById('toastText');

function showToast(message, isError = false) {
  toastText.textContent = message;
  toast.style.background = isError ? 'var(--accent)' : 'var(--success)';
  toast.classList.add('visible');
  
  setTimeout(() => {
    toast.classList.remove('visible');
  }, 3000);
}

// Recent Files
const recentFilesList = document.getElementById('recentFilesList');
let recentFiles = JSON.parse(localStorage.getItem('recentFiles') || '[]');

function renderRecentFiles() {
  if (recentFiles.length === 0) {
    recentFilesList.innerHTML = '<div class="empty-state"><div style="font-size: 2rem; margin-bottom: 0.5rem;">📭</div><div>Пока нет обработанных файлов</div></div>';
    return;
  }
  
  recentFilesList.innerHTML = recentFiles.map(file => 
    '<div class="file-item">' +
    '<div class="file-item__info">' +
    '<div class="file-item__name">' + file.name + '</div>' +
    '<div class="file-item__date">' + formatDate(file.date) + '</div>' +
    '</div>' +
    '<button class="file-item__download" onclick="downloadFile(\'' + file.name + '\')">⬇️ Скачать</button>' +
    '</div>'
  ).join('');
}

function addRecentFile(filename) {
  const file = {
    name: filename,
    date: new Date().toISOString()
  };
  
  recentFiles.unshift(file);
  if (recentFiles.length > 20) {
    recentFiles = recentFiles.slice(0, 20);
  }
  
  localStorage.setItem('recentFiles', JSON.stringify(recentFiles));
  renderRecentFiles();
}

function formatDate(isoString) {
  const date = new Date(isoString);
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function downloadFile(filename) {
  showToast('⬇️ Скачивание ' + filename + '...');
  console.log('Download:', filename);
}

// Initialize
initTheme();
renderRecentFiles();