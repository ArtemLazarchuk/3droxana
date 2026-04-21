<template>
    <div>
        <!DOCTYPE html>
<html lang="uk">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Чат | KPI Assistant</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body class="chat-body">
  <nav class="nav-bar">
    <div class="logo">KPI<span>Assistant</span></div>
    <div class="nav-links">
      <a href="index.html">Головна</a>
      <a href="chat.html">Чат</a>
      <a href="#">Про</a>
      <a href="#">Налаштування</a>
    </div>
  </nav>

  <main class="chat-main">
    <section class="avatar-section">
      <canvas id="avatar-canvas"></canvas>
    </section>

    <section class="chat-section">
      <div id="chat-container" class="chat-container"></div>
      <div class="input-area">
        <input type="text" id="chat-input" placeholder="Напишіть щось..." />
        <button id="send-btn">📩</button>
      </div>
    </section>
  </main>

  <script type="module" src="chat.js"></script>
  <script type="module" src="app.js"></script>
  <script type="module" src="avatar.js"></script>
</body>
</html>

    </div>
  </template>
  
  <script setup>
  // (опціонально)
  </script>
  