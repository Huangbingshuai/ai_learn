"use strict";

(() => {
  const { topics, evidence } = window.QINGZHI_DATA;
  const app = document.querySelector("#app");
  const sheet = document.querySelector("#sheet");
  const sheetContent = document.querySelector("#sheet-content");
  const reviewPanel = document.querySelector("#review-panel");
  const generationMode = document.querySelector("#generation-mode");
  const summaryMode = document.querySelector("#summary-mode");
  const storageKey = "qingzhi-comic.records.v1";
  const groupSize = 20;
  const quizLifetime = 24 * 60 * 60 * 1000;
  const restartCutoff = 30 * 60 * 1000;
  const summaryDelay = 4700;
  const letters = ["A", "B", "C", "D"];
  const typeNames = { single: "单选题", multi: "多选题", judge: "判断题" };
  const dateFormat = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false
  });
  const state = {
    page: "home",
    input: "",
    error: "",
    job: null,
    quiz: null,
    restart: null,
    record: null,
    reviewFrom: "result",
    reviewIndex: 0
  };
  let generationClock;
  let summaryClock;
  let dialogFocus;
  let modalConfirm;
  let storageAvailable = true;
  let records = readRecords();

  function escape(value) {
    const htmlChars = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return String(value).replace(/[&<>"']/g, ch => htmlChars[ch]);
  }

  function count(text) {
    return [...text.trim()].length;
  }

  function normalize(text) {
    return text.normalize("NFKC").trim().toLowerCase().replace(/[\p{P}\p{White_Space}]+/gu, "");
  }

  function findTopic(raw) {
    const match = normalize(raw);
    return topics.find(topic => [topic.title, ...topic.aliases].some(alias => normalize(alias) === match));
  }

  function readRecords() {
    try {
      const saved = localStorage.getItem(storageKey);
      if (!saved) return [];
      const parsed = JSON.parse(saved);
      return Array.isArray(parsed) ? parsed.filter(isRecord) : [];
    } catch {
      storageAvailable = false;
      return [];
    }
  }

  function isRecord(record) {
    return record && typeof record.id === "string" && typeof record.topicTitle === "string" &&
      Number.isFinite(record.completedAt) && Array.isArray(record.takeaways) &&
      Array.isArray(record.answers) && record.answers.length === groupSize &&
      record.answers.every(entry => entry && entry.question && Array.isArray(entry.selected) &&
        Array.isArray(entry.question.options) && Array.isArray(entry.question.correct)) &&
      record.summary && ["pending", "ready", "failed"].includes(record.summary.status);
  }

  function persist() {
    if (!storageAvailable) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(records));
    } catch {
      storageAvailable = false;
      document.querySelector("#storage-note").textContent =
        "当前浏览器无法写入本地存储，这次评审的记录会保留到页面关闭。";
    }
  }

  if (!storageAvailable) {
    document.querySelector("#storage-note").textContent =
      "当前浏览器无法读取本地存储，这次评审的记录会保留到页面关闭。";
  }

  function action(label, key, { secondary = false, disabled = false, value } = {}) {
    const valueAttr = value === undefined ? "" : ` data-value="${escape(value)}"`;
    return `<button type="button" class="action${secondary ? " secondary" : ""}" data-action="${key}"${valueAttr}${disabled ? " disabled" : ""}>${escape(label)}</button>`;
  }

  function row(label, key, { sub = "", mint = false, value } = {}) {
    const valueAttr = value === undefined ? "" : ` data-value="${escape(value)}"`;
    return `<button type="button" class="comic-row${mint ? " mint" : ""}" data-action="${key}"${valueAttr}>
      <span>${escape(label)}${sub ? `<small>${escape(sub)}</small>` : ""}</span><span class="tape" aria-hidden="true">›</span>
    </button>`;
  }

  function tape(label, tone = "") {
    return `<span class="tape ${tone}">${escape(label)}</span>`;
  }

  const pages = {
    home: { title: "轻知闯关", draw: home, tab: "home" },
    generation: { title: "生成练习", draw: generation, back: "cancel-generation" },
    quiz: { title: "练习", draw: quiz, back: "ask-exit" },
    feedback: { title: "练习", draw: feedback, back: "ask-exit" },
    result: { title: "本次结果", draw: result, back: "history" },
    history: { title: "学习记录", draw: history, tab: "history" },
    record: { title: "记录详情", draw: record, back: "history" },
    mistakes: { title: "错题回顾", draw: mistakes, back: "back-to-results" },
    review: { title: "作答回顾", draw: answerReview, back: "back-from-review" }
  };

  function header(page) {
    return `<header class="device-header">
      <div class="header-start">${page.back ? `<button type="button" class="back" aria-label="返回" data-action="${page.back}">‹</button>` : ""}
      <span class="screen-title">${page.title}</span></div>
      <button type="button" class="capsule" aria-label="打开原型评审工具" data-action="show-tools">
        <span class="capsule-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="capsule-ring" aria-hidden="true"></span>
      </button>
    </header>`;
  }

  function tabs(current) {
    return `<nav class="tabs" aria-label="小程序导航">
      ${[["home", "开始学习"], ["history", "学习记录"]].map(([key, label]) =>
        `<button type="button" class="tab" data-action="${key}"${current === key ? ' aria-current="page"' : ""}><span>${label}</span></button>`).join("")}
    </nav>`;
  }

  function render() {
    const page = pages[state.page];
    app.innerHTML = `${header(page)}<section class="screen screen-${state.page}" id="screen">${page.draw()}</section>${page.tab ? tabs(page.tab) : ""}`;
  }

  function go(page, { focus = true } = {}) {
    state.page = page;
    render();
    if (focus) focusPage();
  }

  function focusPage() {
    const heading = app.querySelector("#screen h1") || app.querySelector("#screen");
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
    const deviceTop = app.getBoundingClientRect().top;
    if (deviceTop < 0 || deviceTop > window.innerHeight / 2) app.scrollIntoView({ block: "start" });
  }

  function home() {
    const restart = state.restart;
    const freshGroup = restart && Date.now() + restartCutoff < restart.expiresAt;
    const restartMarkup = restart ? `<section class="restart-card" aria-label="重开练习">
      <p>${freshGroup ? `可以从第 1 题重新开始「${escape(restart.topic.title)}」。` : "上次的题组即将到期，请重新生成。"} </p>
      ${action(freshGroup ? "从第 1 题开始" : "重新生成题组", "restart", { secondary: true })}
    </section>` : "";
    return `${restartMarkup}
      <div class="comic-cover">
        <img src="assets/cover.webp" width="632" height="416" alt="翻开的书里跳出一颗地球，戴眼镜的学生在旁边发问">
        <h1>今天想弄懂<br><span>什么？</span></h1>
        <p class="cover-dialogue">地球忘带<br>校服啦？</p>
      </div>
      <p class="home-caption">一组 20 道题，每题都有讲解</p>
      <form class="topic-form" id="topic-form" novalidate>
        <div class="topic-field">
          <label for="topic" class="visually-hidden">学习主题</label>
          <input type="text" id="topic" name="topic" value="${escape(state.input)}" placeholder="输入主题，例如「为什么会有四季」"
            autocomplete="off" aria-describedby="topic-count topic-error" aria-invalid="${Boolean(state.error)}">
          <span class="char-count" id="topic-count">${count(state.input)} / 80</span>
        </div>
        <p class="field-error" id="topic-error" role="alert"${state.error ? "" : " hidden"}>${escape(state.error)}</p>
        <button type="submit" class="action">生成练习</button>
      </form>
      <section class="topic-picker" aria-labelledby="topic-picker-title">
        <h2 id="topic-picker-title">或者直接挑一个</h2>
        <div class="topic-options">
          ${topics.map(topic => `<button class="topic ${topic.art}" type="button" data-action="topic" data-value="${topic.id}">
            <span class="topic-symbol" aria-hidden="true"></span><span>${escape(topic.title)}</span><span class="topic-arrow" aria-hidden="true">›</span>
          </button>`).join("")}
        </div>
      </section>`;
  }

  function changeInput(event) {
    if (event.target.id !== "topic") return;
    state.input = event.target.value;
    document.querySelector("#topic-count").textContent = count(state.input) + " / 80";
    if (state.error) {
      state.error = "";
      const problem = document.querySelector("#topic-error");
      problem.hidden = true;
      problem.textContent = "";
      event.target.setAttribute("aria-invalid", "false");
    }
  }

  function submitTopic(event) {
    if (event.target.id !== "topic-form") return;
    event.preventDefault();
    const field = event.target.elements.topic;
    const raw = field.value;
    const length = count(raw);
    const topic = findTopic(raw);
    state.input = raw;
    if (length < 2 || length > 80 || !topic) {
      state.error = length === 0 ? "请输入想学的主题，或在下方选一个。"
        : length < 2 || length > 80 ? "主题需要 2–80 字，请调整后再试。"
        : "暂时没有匹配的主题。可以换个说法，或从下方选择已有主题。";
      const message = document.querySelector("#topic-error");
      message.hidden = false;
      message.textContent = state.error;
      field.setAttribute("aria-invalid", "true");
      field.focus();
      return;
    }
    startGeneration(topic);
  }

  const generationFailures = {
    timeout: { title: "题目暂时没生成", description: "生成超时了。你可以再试一次，或换个主题。", canRetry: true },
    sources: { title: "这个主题的依据不足", description: "现有资料还支撑不了一组完整练习。请选择另一个主题。", canRetry: false },
    quality: { title: "这组题没有通过核验", description: "这组题暂时无法用于练习。你可以重新生成，或换个主题。", canRetry: true }
  };

  function generation() {
    const job = state.job;
    const failure = job.status === "failed" ? generationFailures[job.mode] : null;
    const heading = failure ? escape(failure.title) : "正在准备<br><strong>你的 20 道题</strong>";
    return `<div class="stage">
      ${tape(job.topic.title)}
      <div class="manifest${failure ? " is-error" : ""}"><h1>${heading}</h1><span class="spot" aria-hidden="true"></span></div>
      <div id="generation-status" role="status" aria-live="polite">${failure
        ? `<p class="stage-note">${escape(failure.description)}</p>` : generationSteps(job.step)}</div>
      <div class="actions">
        ${failure?.canRetry ? action("重新生成", "retry-generation") : ""}
        ${action(failure ? "换个主题" : "返回选主题", "cancel-generation", { secondary: true })}
      </div>
    </div>`;
  }

  function generationSteps(step) {
    const labels = ["读取主题资料", "生成并核对题目"];
    return `<ol class="step-list">${labels.map((label, index) => {
      const status = index < step ? ["complete", "已完成"] : index === step ? ["current", "进行中"] : ["queued", "等待中"];
      return `<li class="${status[0]}"><span>${label}</span><small>${status[1]}</small></li>`;
    }).join("")}</ol><p class="stage-note">题组就绪后即可开始练习。</p>`;
  }

  function startGeneration(topic, mode = generationMode.value) {
    cancelGeneration(false);
    generationMode.value = "ok";
    state.input = topic.title;
    state.error = "";
    state.restart = null;
    state.quiz = null;
    const job = { topic, mode, status: "working", step: 0 };
    state.job = job;
    go("generation");
    generationClock = setTimeout(() => {
      if (state.job !== job) return;
      job.step = 1;
      document.querySelector("#generation-status").innerHTML = generationSteps(job.step);
      generationClock = setTimeout(() => {
        if (state.job !== job) return;
        if (mode !== "ok") {
          job.status = "failed";
          go("generation");
          return;
        }
        state.job = null;
        beginQuiz({ topic, expiresAt: Date.now() + quizLifetime });
      }, 1350);
    }, 550);
  }

  function cancelGeneration(returnHome = true) {
    clearTimeout(generationClock);
    state.job = null;
    if (returnHome) go("home");
  }

  function beginQuiz(group, sample = false) {
    state.restart = null;
    state.quiz = { ...group, sample, index: 0, selected: [], answers: [], record: null };
    go("quiz");
  }

  function meter(title, index, finished) {
    return `<div class="quiz-top">
      <div class="quiz-meta">${tape(title)}<strong aria-hidden="true">${String(index + 1).padStart(2, "0")} / ${groupSize}</strong></div>
      <div class="progress-marks" role="progressbar" aria-label="练习进度" aria-valuemin="0" aria-valuemax="${groupSize}"
        aria-valuenow="${finished}" aria-valuetext="第 ${index + 1} 题，共 ${groupSize} 题，已完成 ${finished} 题">
        ${Array.from({ length: groupSize }, (_, n) => `<i class="${n < finished ? "done" : n === index ? "current" : ""}"></i>`).join("")}
      </div>
    </div>`;
  }

  function option(question, text, index, selected, receipt = null) {
    const locked = Boolean(receipt);
    const chosen = selected.includes(index);
    const correct = locked && question.correct.includes(index);
    const visual = correct ? "correct" : locked && chosen ? "incorrect" : chosen ? "selected" : "";
    const className = `answer ${question.type === "multi" ? "" : "single"} ${visual}${locked ? " locked" : ""}`;
    const contents = `<span class="answer-letter" aria-hidden="true">${letters[index]}</span>
      <span class="answer-text">${escape(text)}${locked && (correct || chosen) ? `<span class="visually-hidden">${correct ? "，正确答案" : ""}${chosen ? "，我的作答" : ""}</span>` : ""}</span>
      <span class="answer-icon" aria-hidden="true">${correct || (!locked && chosen) ? "✓" : locked && chosen ? "×" : ""}</span>`;
    if (locked) return `<div class="${className}" role="listitem">${contents}</div>`;
    return `<button type="button" class="${className}" data-action="select" data-value="${index}" aria-pressed="${chosen}">${contents}</button>`;
  }

  function quiz() {
    const attempt = state.quiz;
    const question = attempt.topic.questions[attempt.index];
    return `${meter(attempt.topic.title, attempt.index, attempt.answers.length)}
      <section class="question-comic" aria-labelledby="question-heading">
        <div class="question-badges">${tape(typeNames[question.type], "yellow")}${tape("AI 生成 · 演示")}</div>
        <h1 id="question-heading">${escape(question.stem)}</h1>
        <p>${question.type === "multi" ? "可选多个答案，选完再提交。" : "请选择一个答案。"}</p>
      </section>
      <div class="answer-list" role="group" aria-label="选择答案">
        ${question.options.map((text, index) => option(question, text, index, attempt.selected)).join("")}
      </div>
      <div class="quiz-actions">${action("提交答案", "submit-answer", { disabled: !attempt.selected.length })}</div>`;
  }

  function selectOption(index) {
    const attempt = state.quiz;
    if (state.page !== "quiz" || !attempt) return;
    const question = attempt.topic.questions[attempt.index];
    if (!Number.isInteger(index) || index < 0 || index >= question.options.length) return;
    if (question.type === "multi") {
      attempt.selected = attempt.selected.includes(index)
        ? attempt.selected.filter(value => value !== index) : [...attempt.selected, index];
    } else {
      attempt.selected = [index];
    }
    app.querySelectorAll('[data-action="select"]').forEach(button => {
      const active = attempt.selected.includes(Number(button.dataset.value));
      button.classList.toggle("selected", active);
      button.setAttribute("aria-pressed", String(active));
      button.querySelector(".answer-icon").textContent = active ? "✓" : "";
    });
    app.querySelector('[data-action="submit-answer"]').disabled = attempt.selected.length === 0;
  }

  function receipt(question, selected) {
    return {
      question: { ...question, source: { ...evidence[question.evidence] } },
      selected: [...selected].sort((a, b) => a - b),
      correct: selected.length === question.correct.length && question.correct.every(index => selected.includes(index))
    };
  }

  function submitAnswer() {
    const attempt = state.quiz;
    if (state.page !== "quiz" || !attempt?.selected.length || attempt.index !== attempt.answers.length) return;
    const question = attempt.topic.questions[attempt.index];
    attempt.answers.push(receipt(question, attempt.selected));
    if (attempt.answers.length === groupSize) {
      attempt.record = buildRecord(attempt.topic, attempt.answers, attempt.sample, summaryMode.value);
      summaryMode.value = "ok";
      state.record = attempt.record;
      if (!attempt.sample) {
        records = [attempt.record, ...records];
        persist();
      }
      scheduleSummary();
    }
    go("feedback");
  }

  function proceed() {
    const attempt = state.quiz;
    if (state.page !== "feedback") return;
    if (attempt.record) {
      state.record = attempt.record;
      go("result");
      return;
    }
    attempt.index += 1;
    attempt.selected = [];
    go("quiz");
  }

  function selectedLabels(answer, values) {
    return values.map(index => answer.question.type === "judge" ? answer.question.options[index] : letters[index]).join("、");
  }

  function feedback() {
    const attempt = state.quiz;
    const answer = attempt.answers[attempt.index];
    return `${meter(attempt.topic.title, attempt.index, attempt.answers.length)}
      ${receiptView(answer, "live")}
      <div class="quiz-actions">${action(attempt.index === groupSize - 1 ? "查看通关结果" : "下一题", "next-question")}</div>`;
  }

  function receiptView(answer, context) {
    const question = answer.question;
    const incomplete = !answer.correct && question.type === "multi" && answer.selected.every(index => question.correct.includes(index));
    const heading = answer.correct ? "答对啦！" : incomplete ? "还差选项" : "这题答错了";
    return `<div class="feedback${answer.correct ? " is-correct" : ""}">
      <h1>${heading}</h1>
      <img src="assets/student-${answer.correct ? "happy" : "wow"}.webp" alt="">
      <div class="feedback-answers"><span>你的作答 ${escape(selectedLabels(answer, answer.selected))}</span>
        <strong>正确答案 ${escape(selectedLabels(answer, question.correct))}</strong></div>
    </div>
    <p class="review-stem">${escape(question.stem)}</p>
    <div class="answer-list" role="list" aria-label="首次作答结果">
      ${question.options.map((text, index) => option(question, text, index, answer.selected, answer)).join("")}
    </div>
    <section class="speech" aria-label="题目讲解">
      <div class="speech-title"><h2>${answer.correct ? "为什么是这个答案？" : "原来如此！"}</h2>${tape("AI 生成 · 演示")}</div>
      <p>${escape(question.explanation)}</p>
    </section>
    ${row("题目依据 · 查看原文与出处", "evidence", { value: context, mint: true })}`;
  }

  function buildRecord(topic, answers, sample, mode) {
    const id = globalThis.crypto?.randomUUID?.() || `record-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return {
      id, sample, topicTitle: topic.title, takeaways: topic.takeaways.map(point => ({ ...point })),
      completedAt: Date.now(), answers,
      summary: { status: "pending", mode, finishAt: Date.now() + summaryDelay }
    };
  }

  function score(record) {
    return record.answers.filter(answer => answer.correct).length;
  }

  function missed(record) {
    return record.answers.flatMap((answer, index) => answer.correct ? [] : [{ answer, index }]);
  }

  function pendingRecords() {
    return [...new Set([...records, state.record, state.quiz?.record].filter(Boolean))]
      .filter(entry => entry.summary.status === "pending");
  }

  function scheduleSummary() {
    clearTimeout(summaryClock);
    const pending = pendingRecords();
    if (pending.length === 0) return;
    const nextDelay = Math.max(0, Math.min(...pending.map(entry => entry.summary.finishAt)) - Date.now());
    summaryClock = setTimeout(settleSummaries, nextDelay);
  }

  function settleSummaries() {
    let changed = false;
    for (const entry of pendingRecords()) {
      if (entry.summary.finishAt > Date.now()) continue;
      entry.summary.status = entry.summary.mode === "ok" ? "ready" : "failed";
      changed = true;
    }
    if (changed) {
      persist();
      const summary = document.querySelector("#summary");
      if (summary && state.record) summary.innerHTML = summaryContents(state.record);
    }
    scheduleSummary();
  }

  function summaryContents(record) {
    const heading = `<div class="summary-heading"><h2>AI 学习总结</h2>${tape("AI 生成 · 演示", "paper")}</div>`;
    if (record.summary.status === "pending") {
      return heading + "<p>正在整理这次练习的重点和错题。你可以先回顾基础结果。</p>";
    }
    if (record.summary.status === "failed") {
      const canRetry = record.summary.mode === "timeout";
      return heading + `<p>${canRetry ? "这次总结暂时没生成。可以重试，基础结果已保留。"
        : "这次总结没有生成。基础结果和错题仍然可以查看。"}</p>${canRetry ? action("重试生成总结", "retry-summary", { secondary: true }) : ""}`;
    }
    const mistakes = missed(record);
    const uniqueConcepts = [...new Map(mistakes.map(item => [item.answer.question.evidence, item])).values()];
    const followup = uniqueConcepts[0]?.answer.question.source ?? record.answers[0].question.source;
    const topicLinks = record.takeaways.map(point => {
      const questionIndex = record.answers.findIndex(item => item.question.evidence === point.evidence);
      const reference = questionIndex === -1 ? "" : summaryLink(questionIndex);
      return `<li>${escape(point.text ?? point)} ${reference}</li>`;
    }).join("");
    const weakPoints = uniqueConcepts.length
      ? `<ul>${uniqueConcepts.slice(0, 4).map(item => `<li>${escape(item.answer.question.source.concept)} ${summaryLink(item.index)}</li>`).join("")}</ul>`
      : '<p class="review-record-empty">这组题全部答对，可以用下面的建议再巩固一下。</p>';
    return heading + `
      <section class="summary-section"><h3>知识回顾</h3><ul>${topicLinks}</ul></section>
      <section class="summary-section"><h3>${mistakes.length ? "易错点" : "作答回顾"}</h3>${weakPoints}</section>
      <section class="summary-section"><h3>${mistakes.length ? "复习建议" : "巩固建议"}</h3><p>${escape(followup.tip)}</p></section>`;
  }

  function summaryLink(index) {
    return `<button type="button" class="summary-link" data-action="review-answer" data-value="${index}">查看第 ${index + 1} 题</button>`;
  }

  function basicResult(record) {
    const wrong = groupSize - score(record);
    return `<section class="result-section"><h2>这轮学到了什么</h2>
      <ul>${record.takeaways.map(point => `<li>${escape(point.text ?? point)}</li>`).join("")}</ul>
    </section>
    ${wrong ? row(`错题回顾 · ${wrong} 题`, "open-mistakes")
      : '<p class="review-record-empty">这次全部答对，没有错题。</p>'}
    <section class="result-summary" id="summary" aria-live="polite">${summaryContents(record)}</section>`;
  }

  function result() {
    const entry = state.record;
    return `<div class="result-banner">
      <h1>这一回，答完啦！</h1>
      <p>${escape(entry.topicTitle)} · 完成 ${groupSize} 题</p>
      <div class="result-score tape paper" aria-label="答对 ${score(entry)} 道题，共 ${groupSize} 道题">${score(entry)} / ${groupSize}</div>
      <p>${entry.sample ? "示例记录 · 用于评审" : storageAvailable ? "本次答对 · 已保存学习记录" : "本次答对 · 记录暂存在此页"}</p>
      <img src="assets/student-happy.webp" alt="">
    </div>
    ${basicResult(entry)}
    <div class="actions">${action("再学一个主题", "home")}
      ${action("查看学习记录", "history", { secondary: true })}</div>`;
  }

  function history() {
    if (!records.length) {
      return `<div class="empty-panel"><h1>还没有学习记录</h1><p>答完一组练习后，就能在这里回顾成绩和讲解。</p>
        ${action("选个主题开始", "home")}</div>`;
    }
    return `<div class="history-head"><h1>你学过的主题</h1></div>
      <ul class="comic-row-list history-list">${records.map((entry, index) => `<li>${row(entry.topicTitle, "open-record", {
        value: entry.id, mint: index === 0, sub: `${dateFormat.format(entry.completedAt)} · 答对 ${score(entry)} / ${groupSize}`
      })}</li>`).join("")}</ul>`;
  }

  function record() {
    const entry = state.record;
    return `<div class="detail-head">
      <h1>${escape(entry.topicTitle)}</h1>
      <p>完成时间 · ${escape(dateFormat.format(entry.completedAt))}</p>
      <p>完成 ${groupSize} 题 · 答对 ${score(entry)} 题</p>
    </div>${basicResult(entry)}
    <div class="actions">${action("返回学习记录", "history", { secondary: true })}</div>`;
  }

  function mistakes() {
    const wrong = missed(state.record);
    return `<h1>错题回顾</h1><p>${escape(state.record.topicTitle)} · 共 ${wrong.length} 题</p>
      <ul class="comic-row-list review-list">${wrong.map(({ answer, index }) => `<li>${row(
        `第 ${index + 1} 题 · ${answer.question.stem}`, "review-answer", { value: index, sub: typeNames[answer.question.type] }
      )}</li>`).join("")}</ul>
      <div class="actions">${action("返回本次回顾", "back-to-results", { secondary: true })}</div>`;
  }

  function answerReview() {
    const { record, reviewIndex } = state;
    return `${meter(record.topicTitle, reviewIndex, groupSize)}
      ${receiptView(record.answers[reviewIndex], "review")}
      <div class="actions">${action("返回本次回顾", "back-from-review", { secondary: true })}</div>`;
  }

  function startReview(index) {
    if (!Number.isInteger(index) || !state.record?.answers[index]) return;
    if (state.page !== "mistakes") state.reviewFrom = state.page;
    state.reviewIndex = index;
    state.reviewBack = state.page;
    go("review");
  }

  function retrySummary() {
    const current = state.record;
    if (current?.summary.status !== "failed" || current.summary.mode !== "timeout") return;
    current.summary = { status: "pending", mode: "ok", finishAt: Date.now() + summaryDelay };
    persist();
    document.querySelector("#summary").innerHTML = summaryContents(current);
    scheduleSummary();
  }

  function showSheet(title, inner, confirm = null) {
    if (sheet.open) return;
    dialogFocus = document.activeElement;
    modalConfirm = confirm;
    sheetContent.innerHTML = `<header class="sheet-header"><h2 id="sheet-title">${escape(title)}</h2>
      <button type="button" class="sheet-close" data-action="close-sheet" aria-label="关闭">×</button></header>${inner}`;
    sheet.showModal();
  }

  function showEvidence(context) {
    const entry = context === "review" ? state.record?.answers[state.reviewIndex] : state.quiz?.answers[state.quiz.index];
    if (!entry) return;
    const source = entry.question.source;
    showSheet("题目依据", `<h3>${escape(source.title)}</h3><p><small>来源 · 轻知闯关原型预置资料</small></p>
      <blockquote>${escape(source.text)}</blockquote><p><a href="sources.html#${encodeURIComponent(entry.question.evidence)}"
        target="_blank" rel="noopener">在新标签页中查看资料 ↗</a></p>`);
  }

  function requestExit(after = () => go("home")) {
    const quiz = state.quiz;
    if (!quiz || !["quiz", "feedback"].includes(state.page)) {
      after();
      return;
    }
    if (quiz.record) {
      state.quiz = null;
      after();
      return;
    }
    showSheet("要退出这组练习？", `<p>退出后会清除本次作答进度。题组仍有效时，可以从第 1 题重开。</p>
      ${action("继续答题", "close-sheet")}${action("退出练习", "confirm-sheet", { secondary: true })}`, () => {
      state.restart = quiz.sample ? null : { topic: quiz.topic, expiresAt: quiz.expiresAt };
      state.quiz = null;
      after();
    });
  }

  function restartQuiz() {
    const current = state.restart;
    if (!current) return;
    if (Date.now() + restartCutoff >= current.expiresAt) {
      startGeneration(current.topic);
    } else {
      beginQuiz(current);
    }
  }

  function showTools() {
    reviewPanel.open = true;
    reviewPanel.scrollIntoView({ block: "nearest" });
    reviewPanel.querySelector("summary").focus({ preventScroll: true });
  }

  function sampleFeedback() {
    const topic = topics[0];
    const answers = topic.questions.slice(0, 4).map((question, index) =>
      receipt(question, index === 3 ? [0] : question.correct));
    state.quiz = {
      topic, answers, selected: [0], index: 3, sample: true, record: null, expiresAt: Date.now() + quizLifetime
    };
    go("feedback");
  }

  function sampleResult() {
    const topic = topics[0];
    const misses = [3, 8, 13, 16];
    const answers = topic.questions.map((question, index) => {
      const selected = misses.includes(index)
        ? (question.type === "multi" ? [question.correct[0]] : [question.options.findIndex((_, i) => !question.correct.includes(i))])
        : question.correct;
      return receipt(question, selected);
    });
    state.quiz = null;
    state.record = buildRecord(topic, answers, true, summaryMode.value);
    summaryMode.value = "ok";
    go("result");
    scheduleSummary();
  }

  function handleSample(show) {
    if (state.page === "generation") cancelGeneration(false);
    if (["quiz", "feedback"].includes(state.page) && !state.quiz.sample) requestExit(show);
    else show();
  }

  const commands = {
    home() { state.error = ""; go("home"); },
    history() { go("history"); },
    topic(value) {
      const topic = topics.find(entry => entry.id === value);
      if (topic) startGeneration(topic);
    },
    restart: restartQuiz,
    "cancel-generation": () => cancelGeneration(),
    "retry-generation"() {
      if (state.job?.status === "failed" && generationFailures[state.job.mode]?.canRetry) startGeneration(state.job.topic, "ok");
    },
    select: value => selectOption(Number(value)),
    "submit-answer": submitAnswer,
    "next-question": proceed,
    "ask-exit": () => requestExit(),
    "show-tools": showTools,
    evidence: showEvidence,
    "close-sheet": () => sheet.close(),
    "confirm-sheet"() {
      const confirm = modalConfirm;
      sheet.close();
      confirm?.();
    },
    "retry-summary": retrySummary,
    "open-record"(id) {
      const entry = records.find(record => record.id === id);
      if (entry) {
        state.record = entry;
        go("record");
      }
    },
    "open-mistakes"() { state.reviewFrom = state.page; go("mistakes"); },
    "back-to-results": () => go(state.reviewFrom),
    "review-answer": value => startReview(Number(value)),
    "back-from-review": () => go(state.reviewBack),
    "sample-feedback": () => handleSample(sampleFeedback),
    "sample-result": () => handleSample(sampleResult)
  };

  document.addEventListener("click", event => {
    const button = event.target.closest("button[data-action]");
    if (!button || button.disabled) return;
    const command = commands[button.dataset.action];
    if (command) command(button.dataset.value);
  });
  document.addEventListener("input", changeInput);
  document.addEventListener("submit", submitTopic);
  sheet.addEventListener("close", () => {
    modalConfirm = null;
    if (dialogFocus?.isConnected) dialogFocus.focus({ preventScroll: true });
    dialogFocus = null;
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") settleSummaries();
  });

  render();
  settleSummaries();
})();
