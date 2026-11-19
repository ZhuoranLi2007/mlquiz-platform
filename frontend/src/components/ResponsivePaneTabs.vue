<script setup lang="ts">
defineProps<{ modelValue: string; primary: string; secondary: string }>()
const emit = defineEmits<{ 'update:modelValue': [string] }>()
</script>

<template>
  <nav class="mobile-pane-tabs" aria-label="内容面板切换">
    <button :aria-pressed="modelValue==='primary'" @click="emit('update:modelValue','primary')">{{ primary }}</button>
    <button :aria-pressed="modelValue==='secondary'" @click="emit('update:modelValue','secondary')">{{ secondary }}</button>
  </nav>
</template>

<style scoped>
.mobile-pane-tabs{display:none;gap:8px}.mobile-pane-tabs button{flex:1;padding:8px;border:1px solid #dce5f2;border-radius:8px;background:white;color:#536781;font:inherit;cursor:pointer}.mobile-pane-tabs button[aria-pressed=true]{background:#edf4ff;border-color:#8aa8d4;color:#24549a}
@media(max-width:850px){.mobile-pane-tabs{display:flex}}
</style>
