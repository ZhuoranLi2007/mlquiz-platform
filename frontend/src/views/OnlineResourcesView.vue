<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Link, VideoPlay } from '@element-plus/icons-vue'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'

type Row = Record<string, any>

const items = ref<Row[]>([])
const loading = ref(true)
const platforms = ref<{ value: string; label: string; count: number }[]>([])
const scopes = ref<{ value: string; label: string; count: number }[]>([])
const platform = ref('')
const scope = ref('')
const keyword = ref('')
// 封面加载失败时记下来，降级成平台色块，避免出现破图
const brokenCovers = ref<Record<string, boolean>>({})

const PLATFORM_TABS = [
  { key: '', label: '全部' },
  { key: 'bilibili', label: 'B 站视频' },
  { key: 'douyin', label: '抖音视频' },
  { key: 'article', label: '技术帖子' },
]

const allResources = computed(() =>
  items.value.filter((item) => item.content_type !== 'search_entry'))
const visibleItems = computed(() => allResources.value.filter((item) =>
  !platform.value || (platform.value === 'article'
    ? !['bilibili', 'douyin'].includes(item.platform)
    : item.platform === platform.value)))

const rows = computed(() => {
  const search = keyword.value.trim().toLowerCase()
  return visibleItems.value.filter((item) => {
    if (scope.value && item.scope !== scope.value) return false
    if (!search) return true
    return [item.title_zh, item.original_title, item.intro, item.author,
      ...(item.kp_names || [])].join(' ').toLowerCase().includes(search)
  })
})

const counts = computed(() => {
  const out: Record<string, number> = { '': allResources.value.length }
  for (const item of allResources.value) {
    if (item.platform === 'bilibili') out.bilibili = (out.bilibili || 0) + 1
    else if (item.platform === 'douyin') out.douyin = (out.douyin || 0) + 1
    else out.article = (out.article || 0) + 1
  }
  return out
})

function platformTone(platformValue: string) {
  const map: Record<string, string> = {
    bilibili: 'bili', douyin: 'douyin', zhihu: 'zhihu', csdn: 'csdn',
  }
  return map[platformValue] || 'other'
}

function hasCover(item: Row) {
  return Boolean(item.thumbnail) && !brokenCovers.value[item.resource_id]
}

function markBroken(item: Row) {
  brokenCovers.value = { ...brokenCovers.value, [item.resource_id]: true }
}

// 中文标题与原始标题不同时，才展示原文作副标题
function showOriginal(item: Row) {
  return item.original_title && item.original_title !== item.title_zh
}

function openLink(item: Row) {
  if (!item.url) {
    ElMessage.info('这条资源没有登记链接')
    return
  }
  window.open(item.url, '_blank', 'noopener')
}

async function load() {
  loading.value = true
  try {
    const res: any = await api.get('/resources')
    items.value = res.items || []
    platforms.value = res.platforms || []
    scopes.value = res.scopes || []
  } catch (error) {
    ElMessage.error(friendlyError(error, '在线资源加载失败'))
  } finally {
    loading.value = false
  }
}

function switchPlatform(key: string) {
  platform.value = key
}

onMounted(load)
</script>

<template>
  <section class="online-page">
    <div class="toolbar">
      <div class="category-filter" aria-label="平台筛选">
        <button v-for="tab in PLATFORM_TABS" :key="tab.key" type="button"
          class="filter-chip" :class="{ active: platform === tab.key }"
          :aria-pressed="platform === tab.key" @click="switchPlatform(tab.key)">
          {{ tab.label }}<span v-if="counts[tab.key]" class="chip-count">{{ counts[tab.key] }}</span>
        </button>
      </div>
      <div class="toolbar-right">
        <el-select v-model="scope" placeholder="课程匹配" clearable class="scope-select"
          aria-label="按课程匹配筛选">
          <el-option v-for="item in scopes" :key="item.value"
            :label="`${item.label}（${item.count}）`" :value="item.value" />
        </el-select>
        <el-input v-model="keyword" clearable placeholder="搜索标题、知识点"
          aria-label="搜索在线资源" class="toolbar-search" />
      </div>
    </div>

    <div v-loading="loading" class="resource-grid">
      <article v-for="item in rows" :key="item.resource_id" class="resource-card">
        <!-- 左：真实封面（16:9），拿不到就降级成平台色块，绝不用假图 -->
        <div class="res-cover" :class="`tone-${platformTone(item.platform)}`">
          <img v-if="hasCover(item)" :src="item.thumbnail" :alt="`${item.title_zh} 封面`"
            loading="lazy" decoding="async" @error="markBroken(item)" />
          <span v-else class="cover-fallback">
            <el-icon><VideoPlay v-if="item.content_type !== 'article'" /><Link v-else /></el-icon>
            {{ item.platform_label }}
          </span>
        </div>

        <div class="res-body">
          <h3>{{ item.title_zh }}</h3>
          <p v-if="showOriginal(item)" class="res-original">{{ item.original_title }}</p>

          <div class="res-meta">
            <span class="meta-chip">{{ item.platform_label }}</span>
            <span v-if="item.author && item.author !== item.platform_label"
              class="meta-plain">{{ item.author }}</span>
          </div>

          <div v-if="item.kp_names?.length" class="kp-chips">
            <span v-for="name in item.kp_names.slice(0, 2)" :key="name">{{ name }}</span>
          </div>
          <p class="res-intro">{{ item.intro }}</p>

          <div class="res-foot">
            <span class="scope-chip" :class="`scope-${item.scope}`">{{ item.scope_label }}</span>
            <span v-if="item.access_label !== '可访问'" class="access-chip" :class="`access-${item.access_status}`">
              {{ item.access_label }}
            </span>
            <el-button size="small" type="primary" class="open-btn" @click="openLink(item)">
              查看资源
            </el-button>
          </div>
        </div>
      </article>

      <div v-if="!loading && !rows.length" class="empty-state">
        <strong>没有匹配的资源</strong>
        <p>换个平台或关键词试试。</p>
      </div>
    </div>

  </section>
</template>

<style scoped>
.online-page { width: 100%; max-width: 1280px; min-width: 0; margin: 0 auto; }

.toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin: 0 0 14px; padding: 0;
  border: 0; background: transparent; box-shadow: none; flex-wrap: wrap;
}
.category-filter { display: flex; flex-wrap: wrap; gap: 6px; }
.filter-chip {
  height: 34px; padding: 0 14px; border: 1px solid #e5e7eb; border-radius: 999px;
  background: #fff; color: #4b5563; font-size: 13px; cursor: pointer;
}
.filter-chip:hover { border-color: #c7d2fe; color: #4338ca; }
.filter-chip.active { border-color: #c7d2fe; background: #eef2ff; color: #4338ca; font-weight: 600; }
.chip-count { margin-left: 6px; color: #8b93a3; font-size: 12px; }
.toolbar-right { display: flex; align-items: center; gap: 10px; margin-left: auto; }
.scope-select { width: 160px; }
.toolbar-search { width: 200px; }

.resource-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.resource-card {
  display: grid; grid-template-columns: 172px minmax(0, 1fr); gap: 14px;
  padding: 14px; border: 1px solid #e5e7eb; border-radius: 12px; background: #fff;
  min-width: 0;
}
.resource-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 16px rgb(31 41 55 / 6%); }

/* 统一 16:9，object-fit: cover 只做轻微裁边，不拉伸 */
.res-cover {
  position: relative; display: flex; align-items: center; justify-content: center;
  width: 172px; aspect-ratio: 16 / 9; overflow: hidden; border-radius: 8px;
  background: #f4f6fb; color: #fff;
}
.res-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cover-fallback {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  font-size: 12px; letter-spacing: .02em;
}
.cover-fallback :deep(svg) { width: 22px; height: 22px; }
.cover-badge {
  position: absolute; right: 6px; bottom: 6px; padding: 1px 6px;
  border-radius: 4px; background: rgb(15 23 42 / 55%); color: #fff; font-size: 10px;
}
.tone-bili { background: linear-gradient(135deg, #fb7299, #f0567f); }
.tone-douyin { background: linear-gradient(135deg, #1f2937, #4b5563); }
.tone-zhihu { background: linear-gradient(135deg, #0066ff, #3b82f6); }
.tone-csdn { background: linear-gradient(135deg, #c9302c, #e0524e); }
.tone-other { background: linear-gradient(135deg, #6366f1, #818cf8); }

.res-body { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
.res-body h3 {
  margin: 0; font-size: 14.5px; line-height: 1.5; font-weight: 600;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; overflow-wrap: anywhere;
}
.res-original {
  margin: 0; color: #9aa1ae; font-size: 11.5px; line-height: 1.5;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.res-meta { display: flex; align-items: center; gap: 8px; color: #8b93a3; font-size: 12px; }
.meta-chip { padding: 1px 8px; border-radius: 999px; background: #f4f6fb; color: #4b5563; }
.kp-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.kp-chips span { padding: 1px 8px; border-radius: 6px; background: #eef2ff; color: #4338ca; font-size: 11.5px; }
.res-intro {
  margin: 0; color: #697181; font-size: 12.5px; line-height: 1.65;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.res-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: auto; padding-top: 6px; }
.scope-chip, .content-chip, .access-chip { padding: 2px 8px; border-radius: 999px; font-size: 11.5px; }
.scope-in_scope { background: #ecfdf5; color: #047857; }
.scope-partial { background: #eef2ff; color: #4338ca; }
.scope-extension { background: #fff7ed; color: #b45309; }
.scope-unassessed { background: #f4f6fb; color: #6b7280; }
.content-title_only { background: #f4f6fb; color: #6b7280; }
.content-intro_checked { background: #eef2ff; color: #4338ca; }
.content-full_text, .content-subtitle_checked { background: #ecfdf5; color: #047857; }
.access-ok { background: #ecfdf5; color: #047857; }
.access-login_required { background: #fff7ed; color: #b45309; }
.access-restricted { background: #fef2f2; color: #b91c1c; }
.access-dead { background: #f4f6fb; color: #6b7280; }
.open-btn { margin-left: auto; min-height: 30px; font-size: 12.5px; }

.empty-state { grid-column: 1 / -1; padding: 48px 20px; text-align: center; border: 1px dashed #e5e7eb; border-radius: 12px; background: #fbfcfe; }
.empty-state strong { display: block; font-size: 15px; }
.empty-state p { margin: 8px 0 0; color: #8b93a3; font-size: 13px; }

.page-note { margin: 18px 0 0; color: #8b93a3; font-size: 12.5px; line-height: 1.75; }
.page-note strong { color: #6b7280; }

@media (max-width: 1080px) { .resource-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 720px) {
  .toolbar { flex-direction: column; align-items: stretch; }
  .toolbar-right { margin-left: 0; width: 100%; }
  .scope-select, .toolbar-search { width: 100%; }
  .resource-card { grid-template-columns: 128px minmax(0, 1fr); gap: 10px; padding: 10px; }
  .res-cover { width: 128px; }
  .open-btn { margin-left: 0; width: 100%; }
}
</style>
