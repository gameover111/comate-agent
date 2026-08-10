<template>
  <div class="graph-page">
    <div class="graph-toolbar">
      <div class="toolbar-left">
        <span class="page-title">知识图谱</span>
        <span class="toolbar-tip">资料发布后会自动生成关系草稿；可批量补齐存量资料。</span>
        <button class="btn-gold" :disabled="scanning" @click="queueGraphExtractionJobs">
          {{ scanning ? '提交中…' : '扫描全部已发布资料' }}
        </button>
        <button class="btn-ghost" :disabled="loading" @click="loadGraph">刷新</button>
      </div>
      <div class="legend">
        <span class="legend-item"><i style="background:#4A90D9"></i>引用</span>
        <span class="legend-item"><i style="background:#E5533C"></i>替代</span>
        <span class="legend-item"><i style="background:#5FBE63"></i>上下位</span>
        <span class="legend-item"><i style="background:#9B9B9B"></i>关联</span>
        <span class="legend-item dash"><i></i>待确认</span>
      </div>
    </div>
    <p v-if="notice" :class="['notice', notice.type]">{{ notice.text }}</p>

    <div class="graph-body">
      <div ref="graphRef" class="graph-canvas"></div>

      <div class="side-panel">
        <template v-if="selectedNode">
          <h3>{{ selectedNode.title }}</h3>
          <p class="node-meta">{{ typeLabel(selectedNode.knowledge_type) }} · {{ selectedNode.version }} · 生效 {{ selectedNode.effective_at }}</p>
          <h4>关联关系（{{ selectedEdges.length }}）</h4>
          <div v-if="selectedEdges.length" class="relation-list">
            <div v-for="edge in selectedEdges" :key="edge.id" class="relation-item">
              <div class="relation-head">
                <span class="rel-type" :style="relColor(edge.relation_type)">{{ edge.relation_label }}</span>
                <span class="rel-status" :class="edge.status">{{ statusLabel(edge.status) }}</span>
                <span class="rel-direction">{{ edge.direction === 'directed' ? '→' : '—' }}</span>
              </div>
              <div class="rel-target">{{ otherNodeTitle(edge) }}</div>
              <div v-if="edge.evidence" class="rel-evidence">依据：{{ edge.evidence }}</div>
              <div v-if="edge.origin === 'system'" class="rel-origin">系统规则（版本替代）</div>
              <div class="rel-actions">
                <template v-if="edge.status === 'draft'">
                  <button class="mini-btn ok" @click="updateRelation(edge, 'confirmed')">确认</button>
                  <button class="mini-btn danger" @click="updateRelation(edge, 'rejected')">拒绝</button>
                </template>
                <button class="mini-btn" @click="deleteRelation(edge)">删除</button>
              </div>
            </div>
          </div>
          <p v-else class="empty-tip">该文档暂无已录入的关系</p>
        </template>
        <template v-else>
          <h3>知识图谱</h3>
          <p class="empty-tip">点击图中任意节点，查看它与其他文档的关系；关联节点与连线会高亮。</p>
        </template>

        <h4>手动添加关系</h4>
        <div class="add-form">
          <select v-model="newRel.sourceId">
            <option value="">源文档</option>
            <option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.title }}</option>
          </select>
          <select v-model="newRel.targetId">
            <option value="">目标文档</option>
            <option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.title }}</option>
          </select>
          <select v-model="newRel.type">
            <option value="cite">引用</option>
            <option value="supersede">替代</option>
            <option value="parent">上下位</option>
            <option value="related">关联</option>
          </select>
          <select v-model="newRel.direction">
            <option value="undirected">无向</option>
            <option value="directed">有向（源→目标）</option>
          </select>
          <textarea v-model="newRel.evidence" rows="2" placeholder="关系依据（原文片段，可选）"></textarea>
          <button class="btn-gold" :disabled="!newRel.sourceId || !newRel.targetId || creating" @click="createRelation">
            {{ creating ? '提交中…' : '添加关系' }}
          </button>
        </div>

        <div v-if="extractResult" class="extract-result">
          <h4>最近一次抽取结果</h4>
          <p>新增 {{ extractResult.created }} 条，跳过已存在 {{ extractResult.skipped }} 条</p>
          <div v-if="extractResult.unmatched.length" class="unmatched">
            <p class="unmatched-title">未能匹配到目标文档（{{ extractResult.unmatched.length }} 条，可手动添加）：</p>
            <div v-for="(item, idx) in extractResult.unmatched" :key="idx" class="unmatched-item">
              {{ item.relation_type }} → {{ item.target_title }}
              <span v-if="item.evidence" class="rel-evidence">（{{ item.evidence }}）</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import {
  apiAdminCompanyKnowledgeCreateRelation,
  apiAdminCompanyKnowledgeDeleteRelation,
  apiAdminCompanyKnowledgeGraph,
  apiAdminCompanyKnowledgeQueueGraphExtractionJobs,
  apiAdminCompanyKnowledgeUpdateRelation,
} from '../api'

const graphRef = ref(null)
const nodes = ref([])
const edges = ref([])
const loading = ref(false)
const scanning = ref(false)
const creating = ref(false)
const selectedNodeId = ref(null)
const notice = ref(null)
const newRel = ref({ sourceId: '', targetId: '', type: 'cite', direction: 'undirected', evidence: '' })

let chart = null
let noticeTimer = null

const typeLabel = (t) => ({ policy: '制度', faq: '问答', history: '历史', news: '动态', department_knowledge: '部门知识' }[t] || t)
const statusLabel = (s) => ({ draft: '待确认', confirmed: '已确认', rejected: '已拒绝' }[s] || s)
const relColor = (t) => ({ cite: '#4A90D9', supersede: '#E5533C', parent: '#5FBE63', related: '#9B9B9B' }[t] || '#9B9B9B')

const selectedNode = computed(() => nodes.value.find((n) => n.id === selectedNodeId.value) || null)
const selectedEdges = computed(() =>
  edges.value.filter((e) => e.source_id === selectedNodeId.value || e.target_source_id === selectedNodeId.value)
)

const otherNodeTitle = (edge) => {
  const otherId = edge.source_id === selectedNodeId.value ? edge.target_source_id : edge.source_id
  const node = nodes.value.find((n) => n.id === otherId)
  return node ? node.title : '（未知文档）'
}

async function loadGraph() {
  loading.value = true
  try {
    const res = await apiAdminCompanyKnowledgeGraph()
    if (!res.success) { showNotice(res.message || '图谱加载失败', 'error'); return }
    nodes.value = res.data.nodes || []
    edges.value = res.data.edges || []
    renderGraph()
  } catch (error) { showNotice(error.message || '图谱加载失败', 'error') } finally { loading.value = false }
}

function ensureChart() {
  if (!chart && graphRef.value) {
    chart = echarts.init(graphRef.value)
    chart.on('click', (params) => {
      if (params.dataType === 'node') selectedNodeId.value = params.data.id
    })
  }
  return chart
}

function renderGraph() {
  const instance = ensureChart()
  if (!instance) return
  instance.setOption(
    {
      tooltip: { trigger: 'item', formatter: (p) => (p.dataType === 'edge' ? `${p.data.label}` : `<b>${p.data.title}</b><br/>${typeLabel(p.data.knowledge_type)}`) },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion: 320, edgeLength: [90, 160], gravity: 0.12 },
        emphasis: { focus: 'adjacency', blurScope: 'coordinateSystem' },
        label: { show: true, position: 'bottom', fontSize: 11, color: '#555' },
        data: nodes.value.map((n) => ({
          id: n.id, title: n.title, knowledge_type: n.knowledge_type,
          name: n.title, symbolSize: 34,
          itemStyle: { color: nodeColor(n.knowledge_type) },
        })),
        links: edges.value.map((e) => ({
          source: e.source_id, target: e.target_source_id,
          label: { show: true, formatter: e.relation_label, fontSize: 10, color: relColor(e.relation_type) },
          lineStyle: {
            color: relColor(e.relation_type), width: e.status === 'draft' ? 1.5 : 2.5,
            type: e.status === 'draft' ? 'dashed' : 'solid',
          },
          ...(e.direction === 'directed' ? { symbol: ['none', 'arrow'] } : {}),
        })),
      }],
    },
    { notMerge: true }
  )
}

const nodeColor = (t) => ({ policy: '#4A90D9', faq: '#5FBE63', history: '#9B6FD8', news: '#E88D8D', department_knowledge: '#C99A2E' }[t] || '#9B9B9B')

async function queueGraphExtractionJobs() {
  if (!confirm('将为全部已发布且生效的资料生成关系草稿。该操作会在后台调用模型，但不会自动确认任何关系。是否继续？')) return
  scanning.value = true
  try {
    const res = await apiAdminCompanyKnowledgeQueueGraphExtractionJobs()
    if (!res.success) { showNotice(res.message || '提交失败', 'error'); return }
    const queued = res.data?.queued || 0
    const skipped = res.data?.skipped || 0
    showNotice(queued ? `已提交 ${queued} 份资料的关系草稿任务${skipped ? `，${skipped} 份已有进行中任务` : ''}。` : '没有需要提交的资料，请稍后刷新查看任务状态。')
  } catch (error) { showNotice(error.message || '提交失败', 'error') } finally { scanning.value = false }
}

async function createRelation() {
  if (!newRel.value.sourceId || !newRel.value.targetId) return
  creating.value = true
  try {
    const res = await apiAdminCompanyKnowledgeCreateRelation({
      source_id: newRel.value.sourceId,
      target_source_id: newRel.value.targetId,
      relation_type: newRel.value.type,
      direction: newRel.value.direction,
      evidence: newRel.value.evidence,
      origin: 'manual',
    })
    if (!res.success) { showNotice(res.message || '添加失败', 'error'); return }
    newRel.value = { sourceId: '', targetId: '', type: 'cite', direction: 'undirected', evidence: '' }
    showNotice('关系已添加（待确认）')
    await loadGraph()
  } catch (error) { showNotice(error.message || '添加失败', 'error') } finally { creating.value = false }
}

async function updateRelation(edge, status) {
  try {
    const res = await apiAdminCompanyKnowledgeUpdateRelation(edge.id, status)
    if (!res.success) { showNotice(res.message || '操作失败', 'error'); return }
    showNotice(status === 'confirmed' ? '关系已确认，将参与检索' : '关系已拒绝')
    await loadGraph()
  } catch (error) { showNotice(error.message || '操作失败', 'error') }
}

async function deleteRelation(edge) {
  if (!confirm(`删除「${edge.relation_label}」关系？`)) return
  try {
    const res = await apiAdminCompanyKnowledgeDeleteRelation(edge.id)
    if (!res.success) { showNotice(res.message || '删除失败', 'error'); return }
    showNotice('关系已删除')
    await loadGraph()
  } catch (error) { showNotice(error.message || '删除失败', 'error') }
}

function showNotice(text, type = 'info') {
  notice.value = { text, type }
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = null }, 4500)
}

onMounted(async () => {
  await loadGraph()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (noticeTimer) clearTimeout(noticeTimer)
  if (chart) { chart.dispose(); chart = null }
})

function handleResize() {
  if (chart) chart.resize()
}
</script>

<style scoped>
.graph-page { padding: 18px; display: flex; flex-direction: column; height: calc(100vh - 80px); gap: 12px; }
.graph-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.toolbar-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.page-title { font-size: 16px; font-weight: 600; }
.toolbar-tip { color: #8c7d65; font-size: 12px; }
.btn-gold { background: #C99A2E; color: #fff; border: none; border-radius: 6px; padding: 6px 14px; cursor: pointer; }
.btn-gold:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-ghost { background: #fff; color: #555; border: 1px solid #ddd; border-radius: 6px; padding: 6px 14px; cursor: pointer; }
.legend { display: flex; gap: 12px; align-items: center; font-size: 12px; color: #777; }
.legend-item { display: inline-flex; align-items: center; gap: 4px; }
.legend-item i { width: 14px; height: 3px; display: inline-block; border-radius: 2px; }
.legend-item.dash i { border-top: 2px dashed #999; height: 0; width: 14px; }
.notice { margin: 0; padding: 8px 12px; border-radius: 7px; font-size: 13px; }
.notice.info { background: #fff7e1; color: #8b651d; border: 1px solid #ead39a; }
.notice.error { background: #fff0ed; color: #b95039; border: 1px solid #f0c2b6; }
.graph-body { display: flex; gap: 12px; flex: 1; min-height: 0; }
.graph-canvas { flex: 1; background: #fff; border-radius: 10px; border: 1px solid #eee; }
.side-panel { width: 320px; background: #fff; border-radius: 10px; border: 1px solid #eee; padding: 14px; overflow-y: auto; }
.side-panel h3 { margin: 0 0 4px; font-size: 15px; }
.side-panel h4 { margin: 14px 0 8px; font-size: 13px; color: #666; }
.node-meta { margin: 0 0 6px; font-size: 12px; color: #999; }
.relation-list { display: flex; flex-direction: column; gap: 8px; }
.relation-item { border: 1px solid #eee; border-radius: 8px; padding: 8px; }
.relation-head { display: flex; gap: 8px; align-items: center; font-size: 12px; }
.rel-type { font-weight: 600; }
.rel-status { font-size: 11px; padding: 1px 6px; border-radius: 10px; }
.rel-status.draft { background: #FFF3D6; color: #B8860B; }
.rel-status.confirmed { background: #E4F4E9; color: #2E7D32; }
.rel-status.rejected { background: #FBE9E9; color: #C62828; }
.rel-direction { color: #bbb; }
.rel-target { font-size: 13px; margin: 4px 0; }
.rel-evidence { font-size: 11px; color: #999; margin: 2px 0; }
.rel-origin { font-size: 11px; color: #C99A2E; }
.rel-actions { display: flex; gap: 6px; margin-top: 6px; }
.mini-btn { border: 1px solid #ddd; background: #fff; border-radius: 4px; font-size: 11px; padding: 2px 8px; cursor: pointer; }
.mini-btn.ok { border-color: #5FBE63; color: #2E7D32; }
.mini-btn.danger { border-color: #E5533C; color: #C62828; }
.add-form { display: flex; flex-direction: column; gap: 6px; }
.add-form select, .add-form textarea { padding: 6px; border: 1px solid #ddd; border-radius: 6px; font-size: 12px; }
.empty-tip { font-size: 12px; color: #999; }
</style>
