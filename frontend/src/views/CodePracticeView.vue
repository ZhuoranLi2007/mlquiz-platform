<script setup lang="ts">
/**
 * 代码实践（占位）。
 * 用户会另行提供「关键代码手写」题目，本页先只保留入口与空状态，
 * 不预置任何示例题、假数据或模拟成绩。
 */
</script>

<template>
  <main class="code-page">
    <header class="page-head">
      <p class="eyebrow">动手实现</p>
      <h1>代码实践</h1>
      <p>手写关键代码，按题目形式逐步作答。</p>
    </header>

    <section class="code-empty" aria-labelledby="code-empty-title">
      <span class="code-empty-mark" aria-hidden="true">&lt;/&gt;</span>
      <h2 id="code-empty-title">代码题还没导进来</h2>
      <p>这一模块按「关键代码手写」出题，题目到位后会在这里按题型展示、作答并给出解析；
        现在不放假题，也不给模拟成绩。</p>
      <div class="code-empty-actions">
        <router-link to="/compose?tab=bank" class="code-empty-link">先看看题库里有什么</router-link>
        <span class="code-empty-sep">·</span>
        <router-link to="/library" class="code-empty-link">去学习内容库找代码讲义</router-link>
      </div>
    </section>
  </main>
</template>

<style scoped>
.code-page { max-width: 1080px; margin: 0 auto; }
.page-head { margin-bottom: 20px; }
.page-head h1 { margin: 4px 0 6px; font-size: 30px; letter-spacing: -.02em; }
.eyebrow { margin: 0; color: #4f46e5; font-size: 14px; font-weight: 700; letter-spacing: .12em; }
.page-head p:not(.eyebrow) { margin: 0; color: #5f6675; font-size: 14px; }
.code-empty {
  display: grid; justify-items: center; gap: 10px; padding: 72px 24px;
  border: 1px dashed #d8dce6; border-radius: 16px; background: #fff; text-align: center;
}
.code-empty-mark {
  display: grid; place-items: center; width: 56px; height: 56px; border-radius: 16px;
  background: #eef2ff; color: #4338ca; font: 700 20px/1 var(--font-mono, monospace);
}
.code-empty h2 { margin: 8px 0 0; font-size: 19px; }
.code-empty p { max-width: 460px; margin: 0; color: #697181; font-size: 14px; line-height: 1.8; }
.code-empty-link { margin-top: 8px; color: #4338ca; font-size: 14px; font-weight: 650; }
.code-empty-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; justify-content: center; }
.code-empty-sep { color: #c3c8d4; }
</style>
