// ===== Тема =====
const themeToggle = document.getElementById('themeToggle');

function initTheme() {
  const savedTheme = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);
}

themeToggle.addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  const next = current === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
});

// ===== Загрузка статистики =====
async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.error('Не удалось загрузить статистику:', e);
    return null;
  }
}

// ===== Рендер счётчиков =====
function renderCounters(counters) {
  if (!counters) return;
  const animate = (el, target) => {
    const duration = 800;
    const start = performance.now();
    const from = parseInt(el.textContent) || 0;
    const step = (now) => {
      const progress = Math.min((now - start) / duration, 1);
      el.textContent = Math.round(from + (target - from) * progress);
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  animate(document.getElementById('counterDocs'), counters.texts_created || 0);
  animate(document.getElementById('counterUsers'), counters.unique_visitors || 0);
  animate(document.getElementById('counter30d'), counters.recent_30d_count || 0);
  animate(document.getElementById('counterToday'), counters.today_count || 0);
}

// ===== График активности =====
function renderActivityChart(activity) {
  const chart = document.getElementById('activityChart');
  if (!activity || activity.length === 0) {
    chart.innerHTML = '<div class="placeholder">Нет данных</div>';
    return;
  }

  const maxValue = Math.max(...activity.map(d => d.value), 1);

  chart.innerHTML = activity.map(item => {
    const height = Math.max((item.value / maxValue) * 100, item.value > 0 ? 4 : 0);
    return `
      <div class="chart-bar" 
           style="height: ${height}%;" 
           title="${item.date}: ${item.value} документов">
        <div class="chart-bar__label">${item.value}</div>
        <div class="chart-bar__date">${item.date}</div>
      </div>
    `;
  }).join('');
}

// ===== Список пользователей =====
function renderUsersList(users) {
  const list = document.getElementById('usersList');
  if (!users || users.length === 0) {
    list.innerHTML = '<div class="placeholder">Пока нет данных</div>';
    return;
  }

  list.innerHTML = users.map((u, idx) => `
    <div class="user-item">
      <div class="user-item__info">
        <div class="user-item__name">${escapeHtml(u.name)}</div>
        <div class="user-item__rank">#${idx + 1} по активности</div>
      </div>
      <div class="user-item__stats">
        <div class="user-item__count">${u.count}</div>
        <div class="user-item__label">документов</div>
      </div>
    </div>
  `).join('');
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ===== Toast =====
const toast = document.getElementById('toast');
const toastText = document.getElementById('toastText');

function showToast(message, icon = '✅') {
  toast.querySelector('.toast__icon').textContent = icon;
  toastText.textContent = message;
  toast.classList.add('toast--visible');
  setTimeout(() => toast.classList.remove('toast--visible'), 3000);
}

// ===== Init =====
initTheme();

(async () => {
  const stats = await loadStats();
  if (!stats) {
    document.getElementById('activityChart').innerHTML =
      '<div class="placeholder">Не удалось загрузить данные</div>';
    document.getElementById('usersList').innerHTML =
      '<div class="placeholder">Не удалось загрузить данные</div>';
    return;
  }
  renderCounters(stats.counters);
  renderActivityChart(stats.activity_14d);
  renderUsersList(stats.top_users);
})();

// Автообновление раз в 60 секунд
setInterval(async () => {
  const stats = await loadStats();
  if (stats) {
    renderCounters(stats.counters);
    renderActivityChart(stats.activity_14d);
    renderUsersList(stats.top_users);
  }
}, 60000);