<script setup lang="ts">
withDefaults(defineProps<{ scroll?: boolean; label?: string }>(), { scroll: true, label: '页面内容' })
</script>

<template>
  <section class="workbench-page">
    <header v-if="$slots.header" class="workbench-page-header"><slot name="header" /></header>
    <section class="workbench-page-body" :class="{ 'owns-scroll': scroll }" :aria-label="label" :tabindex="scroll ? 0 : undefined">
      <slot />
    </section>
    <footer v-if="$slots.footer" class="workbench-page-footer"><slot name="footer" /></footer>
  </section>
</template>

<style scoped>
.workbench-page{display:flex;flex-direction:column;gap:12px;width:100%;height:100%;min-width:0;min-height:0;overflow:hidden}
.workbench-page-header,.workbench-page-footer{flex:none;min-width:0}
.workbench-page-header{display:flex;flex-direction:column;gap:12px;max-height:55%;overflow:auto}
.workbench-page-body{flex:1;min-width:0;min-height:0;overflow:hidden}
.workbench-page-body.owns-scroll{overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable}
.workbench-page-body:focus-visible{outline:2px solid var(--app-primary);outline-offset:-2px}
@media(max-height:600px){.workbench-page-header{max-height:35%}}
</style>
