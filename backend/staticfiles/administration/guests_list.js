async function loadGuests() {
  try {
    const response = await fetch(window.GUESTS_API);
    if (!response.ok) throw new Error('Network response was not ok');
    const { guests } = await response.json();
    const tbody = document.querySelector('#guestsTable tbody');
    tbody.innerHTML = '';
    guests.forEach(g => {
      const row = document.createElement('tr');
      const created = g.created_at ? String(g.created_at).slice(0, 16).replace('T', ' ') : '';
      row.innerHTML = `
        <td>${g.username}</td>
        <td>${g.guest_number}</td>
        <td>${created}</td>
        <td>
          <a href="/administration/guests/${g.id}/support-chat/">Open Chat</a>
        </td>`;
      tbody.appendChild(row);
    });
  } catch (error) {
    console.error('Error loading guests:', error);
  }
}

document.addEventListener('DOMContentLoaded', loadGuests);
