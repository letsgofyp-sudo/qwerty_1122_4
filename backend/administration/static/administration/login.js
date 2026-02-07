function togglePassword(){
  const pw = document.getElementById('password');
  const t = document.querySelector('.toggle');
  if(pw.type === 'password'){ pw.type = 'text'; t.textContent = 'Hide'; }
  else { pw.type = 'password'; t.textContent = 'Show'; }
}
