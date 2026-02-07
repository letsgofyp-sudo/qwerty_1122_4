async function loadUsers() {
  try {
    const response = await fetch(window.USERS_API);
    if (!response.ok) throw new Error('Network response was not ok');
    const { users } = await response.json();
    const tbody = document.querySelector('#usersTable tbody');
    tbody.innerHTML = '';
    users.forEach(u => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${u.name}</td>
        <td>${u.email}</td>
        <td>${u.status}</td>
        <td>
          <a href="/administration/users/${u.id}/view/">View</a>
          <a href="/administration/users/${u.id}/edit/">Edit</a>
          <a href="/administration/users/${u.id}/support-chat/">Support Chat</a>
          <a href="/administration/users/${u.id}/vehicles/">Vehicles</a>
        </td>`;
      tbody.appendChild(row);
    });
  } catch (error) {
    console.error('Error loading users:', error);
  }
}

document.addEventListener('DOMContentLoaded', loadUsers);