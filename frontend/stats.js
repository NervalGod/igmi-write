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
// Activity Chart
const activityChart = document.getElementById('activityChart');
function generateActivityData() {
  const data = [];
  for (let i = 13; i >= 0; i--) {
    const date = new Date();
    date.setDate(date.getDate() - i);
    data.push({
      date: date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }),
      value: Math.floor(Math.random() * 20) + 5
    });
  }
  return data;
}
function renderActivityChart() {
  const data = generateActivityData();
  const maxValue = Math.max(...data.map(d => d.value));

  activityChart.innerHTML = data.map(item => {
    const height = (item.value / maxValue) * 100;
    return '<div class="chart-bar" style="height: ' + height + '%;" title="' + item.date + ': ' + item.value + ' документов"><div class="chart-bar__label">' + item.value + '</div></div>';
  }).join('');
}
// Active Users
const usersList = document.getElementById('usersList');
function generateUsersData() {
  const users = [
    { name: 'Иванов И.И.', count: 47, department: 'Отдел проектирования' },
    { name: 'Петров П.П.', count: 32, department: 'Инженерный отдел' },
    { name: 'Сидоров С.С.', count: 28, department: 'Отдел изысканий' },
    { name: 'Козлов К.К.', count: 19, department: 'Отдел проектирования' },
    { name: 'Новиков Н.Н.', count: 15, department: 'Инженерный отдел' }
  ];
  return users;
}
function renderUsersList() {
  const users = generateUsersData();

  usersList.innerHTML = users.map(user =>
    '<div class="user-item">' +
    '<div class="user-item__info">' +
    '<div class="user-item__name">' + user.name + '</div>' +
    '<div class="user-item__dept">' + user.department + '</div>' +
    '</div>' +
    '<div class="user-item__stats">' +
    '<div class="user-item__count">' + user.count + '</div>' +
    '<div class="user-item__label">документов</div>' +
    '</div>' +
    '</div>'
  ).join('');
}
// Initialize
initTheme();
renderActivityChart();
renderUsersList();