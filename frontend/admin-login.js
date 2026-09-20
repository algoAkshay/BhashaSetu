document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('button');
  const password = document.getElementById('password');
  const message = document.getElementById('message');
  button.disabled = true;
  message.textContent = '';
  try {
    const response = await fetch('/admin/login', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Admin-Login': '1'},
      body: JSON.stringify({password: password.value})});
    password.value = '';
    if (!response.ok) throw new Error((await response.json()).detail || 'Unable to sign in.');
    location.assign('/admin');
  } catch (error) { message.textContent = error.message; }
  finally { password.value = ''; button.disabled = false; }
});
