<template>
  <div class="graph-page">
    <header class="graph-page-head">
      <div>
        <p class="eyebrow">RAG · 资料关系</p>
        <h1 class="graph-title">知识图谱</h1>
        <p class="graph-subtitle">查看已发布资料之间的脉络；模型生成的关系会先进入待确认状态。</p>
      </div>
      <div class="page-actions">
        <button class="btn-ghost icon-button" :disabled="loading" title="刷新图谱" @click="loadGraph">
          <span aria-hidden="true">↻</span> 刷新
        </button>
        <button class="btn-gold scan-button" :disabled="scanning" @click="queueGraphExtractionJobs">
          <span aria-hidden="true">✦</span> {{ scanning ? '提交中…' : '扫描已发布资料' }}
        </button>
      </div>
    </header>

    <section class="graph-summary" aria-label="图谱概览">
      <article class="summary-card">
        <span class="summary-mark docs" aria-hidden="true">▦</span>
        <div><span>已发布资料</span><strong class="num">{{ nodes.length }}</strong></div>
      </article>
      <article class="summary-card moss">
        <span class="summary-mark" aria-hidden="true">↗</span>
        <div><span>已确认关系</span><strong class="num">{{ confirmedCount }}</strong></div>
      </article>
      <article class="summary-card gold">
        <span class="summary-mark" aria-hidden="true">⌁</span>
        <div><span>待确认关系</span><strong class="num">{{ draftCount }}</strong></div>
      </article>
      <p class="summary-hint"><b>操作提示：</b>点击节点聚焦关联，点击连线查看证据并完成审核。</p>
    </section>

    <section class="graph-toolbar" aria-label="图谱筛选">
      <div class="filter-group">
        <label class="search-field">
          <span aria-hidden="true">⌕</span>
          <input v-model.trim="keyword" type="search" placeholder="搜索资料名称" aria-label="搜索资料名称">
        </label>
        <label class="select-field">
          <span>资料类型</span>
          <select v-model="typeFilter" aria-label="资料类型">
            <option value="all">全部类型</option>
            <option v-for="type in availableTypes" :key="type" :value="type">{{ typeLabel(type) }}</option>
          </select>
        </label>
        <label class="select-field">
          <span>关系状态</span>
          <select v-model="relationFilter" aria-label="关系状态">
            <option value="active">已确认与待确认</option>
            <option value="confirmed">仅已确认</option>
            <option value="draft">仅待确认</option>
            <option value="all">全部关系</option>
          </select>
        </label>
        <button v-if="hasFilter" class="text-button" @click="clearFilters">清除筛选</button>
      </div>
      <div class="legend" aria-label="关系图例">
        <span class="legend-item"><i class="cite"></i>引用</span>
        <span class="legend-item"><i class="supersede"></i>替代</span>
        <span class="legend-item"><i class="parent"></i>上下位</span>
        <span class="legend-item"><i class="related"></i>关联</span>
        <span class="legend-divider"></span>
        <span class="legend-item dashed"><i></i>待确认</span>
      </div>
    </section>

    <div v-if="notice" :class="['notice', notice.type]" role="status">
      <span aria-hidden="true">{{ notice.type === 'error' ? '!' : '✓' }}</span>{{ notice.text }}
    </div>

    <div class="graph-layout">
      <section class="graph-workspace" aria-label="知识图谱画布">
        <div class="workspace-head">
          <div>
            <b>关系视图</b>
            <span>当前显示 {{ visibleNodes.length }} 篇资料、{{ visibleEdges.length }} 条关系</span>
          </div>
          <div class="canvas-controls" aria-label="画布控制">
            <button title="缩小视图" :disabled="graphZoom <= 0.65" @click="changeZoom(-0.15)">−</button>
            <button title="放大视图" :disabled="graphZoom >= 1.6" @click="changeZoom(0.15)">＋</button>
            <button class="fit-button" title="适配并重置视图" @click="resetView">适配视图</button>
          </div>
        </div>

        <div class="graph-stage" :class="{ 'is-empty': !loading && !visibleNodes.length }">
          <div ref="graphRef" class="graph-canvas"></div>
          <div v-if="loading" class="graph-state loading-state"><span class="loading-dot"></span>正在整理资料关系…</div>
          <div v-else-if="!visibleNodes.length" class="graph-state empty-state">
            <span class="empty-icon" aria-hidden="true">⌘</span>
            <b>{{ nodes.length ? '没有匹配的资料' : '图谱等待第一篇资料' }}</b>
            <p>{{ nodes.length ? '试试清除筛选条件，或更换搜索词。' : '发布资料后会在这里显示节点；关系草稿将在后台生成。' }}</p>
            <button v-if="nodes.length" class="btn-ghost" @click="clearFilters">清除筛选</button>
          </div>
          <p v-else class="canvas-hint">拖动画布浏览 · 鼠标滚轮缩放 · 选择节点查看一跳关系</p>
        </div>
      </section>

      <aside class="inspector" aria-label="关系详情">
        <template v-if="selectedEdge">
          <div class="inspector-kicker"><span class="kicker-dot relation"></span>关系详情</div>
          <div class="relation-detail-head">
            <span class="relation-type" :style="{ color: relationColor(selectedEdge.relation_type) }">{{ relationLabel(selectedEdge) }}</span>
            <span :class="['status-pill', selectedEdge.status]">{{ statusLabel(selectedEdge.status) }}</span>
          </div>
          <div class="relation-path">
            <button @click="selectNode(selectedEdge.source_id)">{{ nodeTitle(selectedEdge.source_id) }}</button>
            <span>{{ selectedEdge.direction === 'directed' ? '→' : '—' }}</span>
            <button @click="selectNode(selectedEdge.target_source_id)">{{ nodeTitle(selectedEdge.target_source_id) }}</button>
          </div>
          <div class="evidence-box">
            <span>关系依据</span>
            <p>{{ selectedEdge.evidence || '未提供原文依据' }}</p>
          </div>
          <p class="origin-note">{{ originLabel(selectedEdge.origin) }}</p>
          <div class="relation-actions">
            <template v-if="selectedEdge.status === 'draft'">
              <button class="action-button confirm" :disabled="actingRelationId === selectedEdge.id" @click="updateRelation(selectedEdge, 'confirmed')">确认关系</button>
              <button class="action-button reject" :disabled="actingRelationId === selectedEdge.id" @click="updateRelation(selectedEdge, 'rejected')">驳回</button>
            </template>
            <button class="action-button delete" :disabled="actingRelationId === selectedEdge.id" @click="deleteRelation(selectedEdge)">删除</button>
          </div>
        </template>

        <template v-else-if="selectedNode">
          <div class="inspector-kicker"><span class="kicker-dot"></span>资料详情</div>
          <div class="node-detail-title">
            <span class="type-token" :style="{ color: nodeColor(selectedNode.knowledge_type), background: nodeSoftColor(selectedNode.knowledge_type) }">{{ typeLabel(selectedNode.knowledge_type) }}</span>
            <h2>{{ selectedNode.title }}</h2>
          </div>
          <p class="node-detail-meta">{{ selectedNode.version }} · {{ selectedNode.category || '未分类' }} · 生效 {{ selectedNode.effective_at || '—' }}</p>
          <div class="node-relation-summary">
            <span><b class="num">{{ selectedNodeEdges.length }}</b> 条关联</span>
            <span><b class="num">{{ selectedNodeEdges.filter((edge) => edge.status === 'draft').length }}</b> 待确认</span>
          </div>
          <div class="relation-list-head"><b>关联资料</b><span>点击查看依据</span></div>
          <div v-if="selectedNodeEdges.length" class="relation-list">
            <button v-for="edge in selectedNodeEdges" :key="edge.id" class="relation-item" @click="selectEdge(edge.id)">
              <span class="relation-line" :style="{ background: relationColor(edge.relation_type) }"></span>
              <span class="relation-item-main"><b>{{ otherNodeTitle(edge, selectedNode.id) }}</b><small>{{ relationLabel(edge) }} · {{ statusLabel(edge.status) }}</small></span>
              <span class="relation-arrow">›</span>
            </button>
          </div>
          <div v-else class="inline-empty">该资料尚未建立关系。可手动补充，或等待关系草稿任务完成。</div>
        </template>

        <template v-else>
          <div class="inspector-kicker"><span class="kicker-dot"></span>探索图谱</div>
          <div class="explore-illustration" aria-hidden="true"><i></i><i></i><i></i><em></em><em></em></div>
          <h2>从一篇资料开始</h2>
          <p class="empty-copy">选择画布上的任意节点，查看它的相邻资料、关系依据与审核状态。</p>
          <ul class="explore-list">
            <li><span>1</span>点击节点，聚焦它的一跳关系</li>
            <li><span>2</span>点击连线，核对模型提供的依据</li>
            <li><span>3</span>确认后，关系才会参与检索</li>
          </ul>
        </template>

        <div class="composer-divider"></div>
        <div class="composer-head">
          <div><b>手动添加关系</b><span>补充模型无法识别的资料关联</span></div>
          <button class="composer-toggle" :class="{ active: showComposer }" @click="toggleComposer">{{ showComposer ? '收起' : '添加' }}</button>
        </div>
        <form v-if="showComposer" class="relation-composer" @submit.prevent="createRelation">
          <label>源资料
            <select v-model="newRel.sourceId"><option value="">请选择</option><option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.title }}</option></select>
          </label>
          <label>目标资料
            <select v-model="newRel.targetId"><option value="">请选择</option><option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.title }}</option></select>
          </label>
          <div class="composer-row">
            <label>关系类型
              <select v-model="newRel.type"><option value="cite">引用</option><option value="supersede">替代</option><option value="parent">上下位</option><option value="related">关联</option></select>
            </label>
            <label>方向
              <select v-model="newRel.direction"><option value="undirected">无向</option><option value="directed">源 → 目标</option></select>
            </label>
          </div>
          <label>关系依据 <small>可选</small><textarea v-model="newRel.evidence" rows="3" placeholder="粘贴原文片段，方便后续审核"></textarea></label>
          <button class="btn-gold composer-submit" :disabled="creating || !newRel.sourceId || !newRel.targetId">{{ creating ? '添加中…' : '创建待确认关系' }}</button>
        </form>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
const actingRelationId = ref('')
const keyword = ref('')
const typeFilter = ref('all')
const relationFilter = ref('active')
const selectedNodeId = ref('')
const selectedRelationId = ref('')
const showComposer = ref(false)
const graphZoom = ref(1)
const notice = ref(null)
const newRel = ref({ sourceId: '', targetId: '', type: 'cite', direction: 'undirected', evidence: '' })

let chart = null
let noticeTimer = null

const typeLabel = (type) => ({ policy: '制度', faq: '问答', history: '历史', news: '动态', department_knowledge: '部门知识' }[type] || type || '资料')
const statusLabel = (status) => ({ draft: '待确认', confirmed: '已确认', rejected: '已驳回' }[status] || status)
const relationLabel = (edge) => edge?.relation_label || ({ cite: '引用', supersede: '替代', parent: '上下位', related: '关联' }[edge?.relation_type] || '关联')
const originLabel = (origin) => ({ llm: '由模型抽取，等待人工审核', manual: '由管理员手动创建', system: '由系统规则自动维护' }[origin] || '关系来源未知')
const relationColor = (type) => ({ cite: '#4A82B8', supersede: '#B5564F', parent: '#5F8A66', related: '#8A8374' }[type] || '#8A8374')
const nodeColor = (type) => ({ policy: '#3B739F', faq: '#5F8A66', history: '#8664A7', news: '#B56B5E', department_knowledge: '#B28128' }[type] || '#7A867D')
const nodeSoftColor = (type) => ({ policy: '#E6F0F6', faq: '#E5F0E7', history: '#F0E8F6', news: '#F7E8E4', department_knowledge: '#F8EFD8' }[type] || '#EDF0EC')

const availableTypes = computed(() => [...new Set(nodes.value.map((node) => node.knowledge_type).filter(Boolean))])
const confirmedCount = computed(() => edges.value.filter((edge) => edge.status === 'confirmed').length)
const draftCount = computed(() => edges.value.filter((edge) => edge.status === 'draft').length)
const hasFilter = computed(() => keyword.value || typeFilter.value !== 'all' || relationFilter.value !== 'active')
const visibleNodes = computed(() => {
  const normalizedKeyword = keyword.value.toLocaleLowerCase()
  return nodes.value.filter((node) => {
    const matchesType = typeFilter.value === 'all' || node.knowledge_type === typeFilter.value
    const matchesKeyword = !normalizedKeyword || node.title.toLocaleLowerCase().includes(normalizedKeyword)
    return matchesType && matchesKeyword
  })
})
const visibleNodeIds = computed(() => new Set(visibleNodes.value.map((node) => node.id)))
const visibleEdges = computed(() => edges.value.filter((edge) => {
  const matchesNodes = visibleNodeIds.value.has(edge.source_id) && visibleNodeIds.value.has(edge.target_source_id)
  return matchesNodes && matchesRelationFilter(edge)
}))
const selectedNode = computed(() => nodes.value.find((node) => node.id === selectedNodeId.value) || null)
const selectedEdge = computed(() => edges.value.find((edge) => edge.id === selectedRelationId.value) || null)
const selectedNodeEdges = computed(() => {
  if (!selectedNodeId.value) return []
  return edges.value
    .filter((edge) => edge.source_id === selectedNodeId.value || edge.target_source_id === selectedNodeId.value)
    .filter(matchesRelationFilter)
    .sort((a, b) => (a.status === 'draft' ? -1 : 0) - (b.status === 'draft' ? -1 : 0))
})

function matchesRelationFilter(edge) {
  if (relationFilter.value === 'all') return true
  if (relationFilter.value === 'active') return edge.status !== 'rejected'
  return edge.status === relationFilter.value
}

function nodeTitle(id) {
  return nodes.value.find((node) => node.id === id)?.title || '未知资料'
}

function otherNodeTitle(edge, nodeId) {
  return nodeTitle(edge.source_id === nodeId ? edge.target_source_id : edge.source_id)
}

function shortTitle(title) {
  const plain = String(title || '').replace(/[{}]/g, '')
  return plain.length > 16 ? `${plain.slice(0, 16)}…` : plain
}

function escapeHtml(value) {
  return String(value || '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]))
}

function clearSelection() {
  selectedNodeId.value = ''
  selectedRelationId.value = ''
}

function clearFilters() {
  keyword.value = ''
  typeFilter.value = 'all'
  relationFilter.value = 'active'
}

function selectNode(id) {
  selectedNodeId.value = id
  selectedRelationId.value = ''
  renderGraph()
}

function selectEdge(id) {
  selectedRelationId.value = id
  selectedNodeId.value = ''
  renderGraph()
}

function resetView() {
  graphZoom.value = 1
  clearSelection()
  renderGraph()
}

function changeZoom(delta) {
  graphZoom.value = Math.min(1.6, Math.max(0.65, Number((graphZoom.value + delta).toFixed(2))))
  renderGraph()
}

async function loadGraph() {
  loading.value = true
  try {
    const response = await apiAdminCompanyKnowledgeGraph()
    if (!response.success) {
      showNotice(response.message || '图谱加载失败', 'error')
      return
    }
    nodes.value = response.data.nodes || []
    edges.value = response.data.edges || []
    if (!nodes.value.some((node) => node.id === selectedNodeId.value)) selectedNodeId.value = ''
    if (!edges.value.some((edge) => edge.id === selectedRelationId.value)) selectedRelationId.value = ''
    renderGraph()
  } catch (error) {
    showNotice(error.message || '图谱加载失败', 'error')
  } finally {
    loading.value = false
  }
}

function ensureChart() {
  if (!chart && graphRef.value) {
    chart = echarts.init(graphRef.value)
    chart.on('click', (params) => {
      if (params.dataType === 'node') selectNode(params.data.id)
      else if (params.dataType === 'edge') selectEdge(params.data.id)
      else {
        clearSelection()
        renderGraph()
      }
    })
  }
  return chart
}

function renderGraph() {
  const instance = ensureChart()
  if (!instance) return
  const focusIds = new Set()
  if (selectedNodeId.value) {
    focusIds.add(selectedNodeId.value)
    edges.value.forEach((edge) => {
      if (edge.source_id === selectedNodeId.value) focusIds.add(edge.target_source_id)
      if (edge.target_source_id === selectedNodeId.value) focusIds.add(edge.source_id)
    })
  }
  if (selectedEdge.value) {
    focusIds.add(selectedEdge.value.source_id)
    focusIds.add(selectedEdge.value.target_source_id)
  }
  const hasFocus = focusIds.size > 0

  instance.setOption({
    animationDuration: 280,
    animationEasingUpdate: 'cubicOut',
    tooltip: {
      trigger: 'item',
      confine: true,
      backgroundColor: '#21302B',
      borderWidth: 0,
      textStyle: { color: '#fff', fontSize: 12 },
      extraCssText: 'box-shadow:0 10px 26px rgba(30,53,44,.18);border-radius:8px;padding:9px 11px;',
      formatter: (params) => {
        const data = params.data || {}
        if (params.dataType === 'edge') return `<b>${escapeHtml(data.relation_label)}</b><br/>${escapeHtml(statusLabel(data.status))}`
        return `<b>${escapeHtml(data.title)}</b><br/>${escapeHtml(typeLabel(data.knowledge_type))} · ${escapeHtml(data.version)}`
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      zoom: graphZoom.value,
      draggable: true,
      cursor: 'grab',
      force: { repulsion: 800, edgeLength: [105, 195], gravity: 0.08, layoutAnimation: true },
      emphasis: { focus: 'adjacency', blurScope: 'coordinateSystem', lineStyle: { width: 3 }, scale: 1.06 },
      label: { show: true, position: 'inside' },
      data: visibleNodes.value.map((node) => {
        const isSelected = selectedNodeId.value === node.id || (selectedEdge.value && (selectedEdge.value.source_id === node.id || selectedEdge.value.target_source_id === node.id))
        const isDimmed = hasFocus && !focusIds.has(node.id)
        return {
          id: node.id,
          name: node.title,
          title: node.title,
          version: node.version,
          knowledge_type: node.knowledge_type,
          symbol: 'roundRect',
          symbolSize: [152, 58],
          itemStyle: {
            color: '#FFFDF8',
            borderColor: nodeColor(node.knowledge_type),
            borderWidth: isSelected ? 3 : 1.4,
            opacity: isDimmed ? 0.22 : 1,
            shadowBlur: isSelected ? 16 : 6,
            shadowColor: isSelected ? `${nodeColor(node.knowledge_type)}55` : 'rgba(47,60,50,.11)',
            shadowOffsetY: 3,
          },
          label: {
            formatter: `{type|${typeLabel(node.knowledge_type)}}\n{title|${shortTitle(node.title)}}`,
            rich: {
              type: { color: nodeColor(node.knowledge_type), fontSize: 10, fontWeight: 600, lineHeight: 16 },
              title: { color: '#2B3932', fontSize: 12, fontWeight: 600, lineHeight: 18, width: 128, overflow: 'truncate' },
            },
          },
        }
      }),
      links: visibleEdges.value.map((edge) => {
        const isSelected = selectedRelationId.value === edge.id
        const isAdjacent = selectedNodeId.value && (edge.source_id === selectedNodeId.value || edge.target_source_id === selectedNodeId.value)
        const isDimmed = hasFocus && !isSelected && !isAdjacent && !(selectedEdge.value && (edge.source_id === selectedEdge.value.source_id || edge.target_source_id === selectedEdge.value.target_source_id))
        return {
          id: edge.id,
          source: edge.source_id,
          target: edge.target_source_id,
          relation_label: relationLabel(edge),
          status: edge.status,
          symbol: edge.direction === 'directed' ? ['none', 'arrow'] : ['none', 'none'],
          symbolSize: edge.direction === 'directed' ? [0, 8] : [0, 0],
          lineStyle: {
            color: relationColor(edge.relation_type),
            width: isSelected ? 3.1 : edge.status === 'draft' ? 1.5 : 2.1,
            type: edge.status === 'draft' ? 'dashed' : 'solid',
            opacity: isDimmed ? 0.12 : edge.status === 'rejected' ? 0.35 : 0.85,
          },
          label: {
            show: Boolean(isSelected || isAdjacent),
            formatter: relationLabel(edge),
            color: relationColor(edge.relation_type),
            fontSize: 11,
            fontWeight: 600,
            backgroundColor: '#FFFDF8',
            padding: [2, 4],
            borderRadius: 3,
          },
        }
      }),
    }],
  }, { notMerge: true })
}

async function queueGraphExtractionJobs() {
  if (!confirm('将为全部已发布且生效的资料生成关系草稿。该操作会在后台调用模型，但不会自动确认任何关系。是否继续？')) return
  scanning.value = true
  try {
    const response = await apiAdminCompanyKnowledgeQueueGraphExtractionJobs()
    if (!response.success) {
      showNotice(response.message || '提交失败', 'error')
      return
    }
    const queued = response.data?.queued || 0
    const skipped = response.data?.skipped || 0
    showNotice(queued ? `已提交 ${queued} 份资料的关系草稿任务${skipped ? `，另有 ${skipped} 份无需重复处理` : ''}。` : '没有需要提交的资料，请稍后刷新查看任务状态。')
  } catch (error) {
    showNotice(error.message || '提交失败', 'error')
  } finally {
    scanning.value = false
  }
}

function toggleComposer() {
  showComposer.value = !showComposer.value
  if (showComposer.value && selectedNode.value && !newRel.value.sourceId) newRel.value.sourceId = selectedNode.value.id
}

async function createRelation() {
  if (!newRel.value.sourceId || !newRel.value.targetId) return
  if (newRel.value.sourceId === newRel.value.targetId) {
    showNotice('源资料和目标资料不能相同', 'error')
    return
  }
  creating.value = true
  try {
    const response = await apiAdminCompanyKnowledgeCreateRelation({
      source_id: newRel.value.sourceId,
      target_source_id: newRel.value.targetId,
      relation_type: newRel.value.type,
      direction: newRel.value.direction,
      evidence: newRel.value.evidence,
      origin: 'manual',
    })
    if (!response.success) {
      showNotice(response.message || '添加失败', 'error')
      return
    }
    newRel.value = { sourceId: '', targetId: '', type: 'cite', direction: 'undirected', evidence: '' }
    showComposer.value = false
    showNotice('关系已创建，等待确认后才会参与检索')
    await loadGraph()
  } catch (error) {
    showNotice(error.message || '添加失败', 'error')
  } finally {
    creating.value = false
  }
}

async function updateRelation(edge, status) {
  actingRelationId.value = edge.id
  try {
    const response = await apiAdminCompanyKnowledgeUpdateRelation(edge.id, status)
    if (!response.success) {
      showNotice(response.message || '操作失败', 'error')
      return
    }
    showNotice(status === 'confirmed' ? '关系已确认，将参与检索' : '关系已驳回，不会参与检索')
    await loadGraph()
  } catch (error) {
    showNotice(error.message || '操作失败', 'error')
  } finally {
    actingRelationId.value = ''
  }
}

async function deleteRelation(edge) {
  if (!confirm(`删除「${relationLabel(edge)}」关系？`)) return
  actingRelationId.value = edge.id
  try {
    const response = await apiAdminCompanyKnowledgeDeleteRelation(edge.id)
    if (!response.success) {
      showNotice(response.message || '删除失败', 'error')
      return
    }
    clearSelection()
    showNotice('关系已删除')
    await loadGraph()
  } catch (error) {
    showNotice(error.message || '删除失败', 'error')
  } finally {
    actingRelationId.value = ''
  }
}

function showNotice(text, type = 'success') {
  notice.value = { text, type }
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = null }, 5000)
}

function handleResize() {
  chart?.resize()
}

watch([keyword, typeFilter, relationFilter], () => {
  clearSelection()
  renderGraph()
})

onMounted(async () => {
  await loadGraph()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (noticeTimer) clearTimeout(noticeTimer)
  if (chart) {
    chart.dispose()
    chart = null
  }
})
</script>

<style scoped>
.graph-page { display:flex; flex-direction:column; gap:16px; min-height:calc(100vh - var(--top-h) - 48px); }
.graph-page-head { display:flex; justify-content:space-between; align-items:flex-end; gap:20px; }
.eyebrow { color:var(--gold); font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
.graph-title { margin-top:2px; color:var(--ink); font-size:24px; letter-spacing:.01em; line-height:1.25; }
.graph-subtitle { margin-top:5px; color:var(--ink-soft); font-size:13px; }
.page-actions { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
.icon-button, .scan-button { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }
.icon-button { padding:8px 13px; }
.scan-button { padding:9px 15px; }
.graph-summary { display:grid; grid-template-columns:repeat(3, minmax(130px, 172px)) minmax(210px, 1fr); gap:10px; }
.summary-card { min-height:72px; display:flex; align-items:center; gap:10px; padding:12px 14px; border:1px solid var(--line); border-radius:10px; background:var(--card); }
.summary-card > div { display:flex; flex-direction:column; gap:1px; }
.summary-card span:not(.summary-mark) { color:var(--ink-soft); font-size:11px; }
.summary-card strong { color:var(--ink); font-size:22px; line-height:1.1; }
.summary-mark { display:grid; place-items:center; width:32px; height:32px; border-radius:9px; background:#EDF3F6; color:#3B739F; font-size:18px; font-weight:700; }
.summary-card.moss .summary-mark { background:#E5F0E7; color:var(--moss); }
.summary-card.gold .summary-mark { background:var(--gold-soft); color:#966D16; }
.summary-hint { align-self:stretch; display:flex; align-items:center; padding:0 16px; border-left:3px solid var(--gold); border-radius:0 8px 8px 0; background:#F8F4E9; color:var(--ink-soft); font-size:12px; }
.summary-hint b { color:var(--ink); margin-right:4px; }
.graph-toolbar { display:flex; justify-content:space-between; align-items:center; gap:12px; min-height:38px; }
.filter-group, .legend { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.search-field { display:flex; align-items:center; gap:7px; width:206px; padding:0 10px; border:1px solid var(--line); border-radius:7px; background:var(--card); color:var(--ink-soft); }
.search-field:focus-within { border-color:var(--gold); box-shadow:0 0 0 3px rgba(201,154,46,.11); }
.search-field input { width:100%; height:34px; border:0; outline:0; background:transparent; color:var(--ink); font-size:12px; }
.search-field input::placeholder { color:#A8A99F; }
.select-field { display:flex; align-items:center; gap:7px; padding:0 9px; border:1px solid var(--line); border-radius:7px; background:var(--card); color:var(--ink-soft); font-size:11px; }
.select-field select { height:34px; max-width:116px; border:0; outline:0; background:transparent; color:var(--ink); font-size:12px; }
.text-button { border:0; background:transparent; color:#8A6A1C; font-size:12px; padding:5px 4px; }
.text-button:hover { color:var(--gold); text-decoration:underline; }
.legend { justify-content:flex-end; color:var(--ink-soft); font-size:11px; }
.legend-item { display:inline-flex; align-items:center; gap:4px; white-space:nowrap; }
.legend-item i { display:inline-block; width:15px; height:3px; border-radius:3px; }
.legend-item i.cite { background:#4A82B8; }.legend-item i.supersede { background:#B5564F; }.legend-item i.parent { background:#5F8A66; }.legend-item i.related { background:#8A8374; }
.legend-item.dashed i { height:0; border-top:2px dashed #A99874; }
.legend-divider { width:1px; height:14px; margin:0 2px; background:var(--line); }
.notice { display:flex; align-items:center; gap:7px; padding:9px 12px; border-radius:8px; font-size:13px; }
.notice > span { display:grid; place-items:center; width:17px; height:17px; border-radius:50%; font-size:11px; font-weight:700; }
.notice.success { border:1px solid #C8DDCC; background:#EAF4EC; color:#3F6D49; }.notice.success > span { background:#5F8A66; color:#fff; }
.notice.error { border:1px solid #EBC9C4; background:#FBEDEA; color:#9D4841; }.notice.error > span { background:#B5564F; color:#fff; }
.graph-layout { display:grid; grid-template-columns:minmax(0, 1fr) 350px; gap:14px; min-height:560px; flex:1; }
.graph-workspace, .inspector { border:1px solid var(--line); border-radius:12px; background:var(--card); overflow:hidden; }
.graph-workspace { display:flex; flex-direction:column; min-width:0; }
.workspace-head { display:flex; justify-content:space-between; align-items:center; gap:12px; min-height:54px; padding:11px 14px 10px 16px; border-bottom:1px solid var(--line); }
.workspace-head > div:first-child { display:flex; align-items:baseline; gap:8px; }
.workspace-head b { color:var(--ink); font-size:13px; }.workspace-head span { color:var(--ink-soft); font-size:11px; }
.canvas-controls { display:flex; align-items:center; gap:4px; padding:3px; border:1px solid var(--line); border-radius:7px; background:#FBFAF6; }
.canvas-controls button { min-width:25px; height:25px; border:0; border-radius:4px; background:transparent; color:var(--ink-soft); font-size:16px; line-height:1; }
.canvas-controls button:hover:not(:disabled) { background:var(--gold-soft); color:#8A6A1C; }.canvas-controls button:disabled { opacity:.35; cursor:not-allowed; }
.canvas-controls .fit-button { width:auto; padding:0 7px; font-size:11px; }
.graph-stage { position:relative; flex:1; min-height:505px; overflow:hidden; background:radial-gradient(circle at 50% 42%, #FFFDF8 0, #FBF9F2 57%, #F4F0E6 100%); }
.graph-stage::before { content:''; position:absolute; inset:0; pointer-events:none; opacity:.34; background-image:radial-gradient(#DAD1BF .75px, transparent .75px); background-size:18px 18px; }
.graph-stage.is-empty { background:#FBFAF6; }.graph-stage.is-empty::before { display:none; }
.graph-canvas { position:absolute; inset:0; z-index:1; }
.graph-state { position:absolute; inset:0; z-index:2; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; color:var(--ink-soft); }
.loading-state { flex-direction:row; gap:9px; background:rgba(255,253,248,.56); font-size:13px; }.loading-dot { width:9px; height:9px; border-radius:50%; background:var(--gold); box-shadow:15px 0 0 rgba(201,154,46,.35), -15px 0 0 rgba(201,154,46,.35); animation:loading-breathe 1.1s ease-in-out infinite; }
.empty-state { padding:28px; }.empty-state b { margin-top:10px; color:var(--ink); font-size:15px; }.empty-state p { max-width:280px; margin:5px 0 14px; font-size:12px; }.empty-icon { display:grid; place-items:center; width:48px; height:48px; border:1px solid #DCCFAF; border-radius:15px; background:#FFF7E4; color:#A87920; font-size:24px; }
.canvas-hint { position:absolute; z-index:3; left:14px; bottom:12px; padding:5px 8px; border:1px solid rgba(226,220,203,.9); border-radius:5px; background:rgba(255,253,248,.88); color:#8A887D; font-size:10px; pointer-events:none; }
.inspector { padding:18px; overflow-y:auto; }.inspector-kicker { display:flex; align-items:center; gap:6px; color:var(--ink-soft); font-size:11px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; }.kicker-dot { width:7px; height:7px; border-radius:50%; background:var(--gold); box-shadow:0 0 0 3px var(--gold-soft); }.kicker-dot.relation { background:#5F8A66; box-shadow:0 0 0 3px #E5F0E7; }
.inspector h2 { color:var(--ink); font-size:17px; line-height:1.42; }.node-detail-title { margin-top:12px; }.node-detail-title h2 { margin-top:8px; }.type-token { display:inline-flex; padding:3px 7px; border-radius:4px; font-size:11px; font-weight:700; }.node-detail-meta { margin-top:7px; color:var(--ink-soft); font-size:11px; line-height:1.7; }.node-relation-summary { display:flex; gap:16px; margin-top:15px; padding:9px 10px; border-radius:8px; background:var(--bg); color:var(--ink-soft); font-size:11px; }.node-relation-summary b { color:var(--ink); font-size:15px; }
.relation-list-head { display:flex; justify-content:space-between; align-items:baseline; margin:18px 0 7px; }.relation-list-head b { color:var(--ink); font-size:13px; }.relation-list-head span { color:var(--ink-soft); font-size:10px; }.relation-list { display:flex; flex-direction:column; gap:6px; }.relation-item { display:flex; align-items:center; width:100%; gap:8px; padding:9px; border:1px solid var(--line); border-radius:8px; background:#FFFEFB; text-align:left; transition:border-color .15s, transform .15s; }.relation-item:hover { border-color:#D2B66C; transform:translateX(1px); }.relation-line { flex:0 0 3px; width:3px; height:27px; border-radius:4px; }.relation-item-main { display:flex; min-width:0; flex:1; flex-direction:column; gap:1px; }.relation-item-main b { overflow:hidden; color:var(--ink); font-size:12px; text-overflow:ellipsis; white-space:nowrap; }.relation-item-main small { color:var(--ink-soft); font-size:10px; }.relation-arrow { color:#A0A69F; font-size:19px; line-height:1; }
.inline-empty { margin-top:12px; padding:11px; border:1px dashed #D9D1BF; border-radius:8px; background:#FBF9F2; color:var(--ink-soft); font-size:11px; line-height:1.65; }.explore-illustration { position:relative; width:124px; height:76px; margin:22px auto 13px; }.explore-illustration i { position:absolute; display:block; width:18px; height:18px; border:4px solid #FFFDF8; border-radius:50%; background:var(--gold); box-shadow:0 0 0 1px #DCC78B; }.explore-illustration i:nth-child(1) { left:7px; top:38px; }.explore-illustration i:nth-child(2) { left:53px; top:8px; background:#5F8A66; box-shadow:0 0 0 1px #BFD1C2; }.explore-illustration i:nth-child(3) { right:6px; bottom:6px; background:#3B739F; box-shadow:0 0 0 1px #B9D0E0; }.explore-illustration em { position:absolute; display:block; height:1px; transform-origin:left center; background:#CFC4AB; }.explore-illustration em:nth-of-type(1) { left:23px; top:44px; width:44px; transform:rotate(-34deg); }.explore-illustration em:nth-of-type(2) { left:68px; top:27px; width:47px; transform:rotate(30deg); }.empty-copy { margin-top:6px; color:var(--ink-soft); font-size:12px; line-height:1.7; }.explore-list { display:flex; flex-direction:column; gap:8px; margin-top:16px; list-style:none; }.explore-list li { display:flex; align-items:center; gap:8px; color:var(--ink-soft); font-size:11px; }.explore-list span { display:grid; place-items:center; width:17px; height:17px; border-radius:50%; background:var(--gold-soft); color:#896516; font-size:10px; font-weight:700; }
.relation-detail-head { display:flex; justify-content:space-between; align-items:center; margin-top:14px; }.relation-type { font-size:18px; font-weight:700; }.status-pill { padding:3px 7px; border-radius:999px; font-size:10px; font-weight:600; }.status-pill.draft { background:#FFF1CE; color:#956C18; }.status-pill.confirmed { background:#E5F0E7; color:#416E4B; }.status-pill.rejected { background:#F7E5E2; color:#9D4841; }.relation-path { display:flex; align-items:center; gap:7px; margin-top:13px; padding:10px; border-radius:8px; background:var(--bg); }.relation-path button { overflow:hidden; min-width:0; flex:1; border:0; background:transparent; color:var(--ink); font-size:11px; text-align:left; text-overflow:ellipsis; white-space:nowrap; }.relation-path button:hover { color:#8A6A1C; text-decoration:underline; }.relation-path span { color:var(--gold); font-weight:700; }.evidence-box { margin-top:14px; padding:11px; border-left:3px solid var(--gold); border-radius:0 8px 8px 0; background:#FBF7EC; }.evidence-box span { color:#9B854E; font-size:10px; font-weight:700; }.evidence-box p { margin-top:4px; color:#526058; font-size:12px; line-height:1.7; }.origin-note { margin-top:9px; color:var(--ink-soft); font-size:11px; }.relation-actions { display:flex; gap:7px; margin-top:16px; }.action-button { flex:1; min-height:32px; border:1px solid var(--line); border-radius:6px; background:#fff; font-size:12px; font-weight:600; }.action-button.confirm { border-color:#9DBEA4; background:#EFF7F0; color:#477751; }.action-button.reject, .action-button.delete { color:#9D4841; }.action-button.reject { border-color:#E6BAB4; background:#FFF6F4; }.action-button.delete { flex:0 0 auto; padding:0 10px; }.action-button:hover:not(:disabled) { filter:brightness(.98); }.action-button:disabled { opacity:.5; cursor:not-allowed; }
.composer-divider { height:1px; margin:22px 0 14px; background:var(--line); }.composer-head { display:flex; justify-content:space-between; gap:8px; }.composer-head > div { display:flex; flex-direction:column; }.composer-head b { color:var(--ink); font-size:13px; }.composer-head span { margin-top:2px; color:var(--ink-soft); font-size:10px; line-height:1.45; }.composer-toggle { flex:0 0 auto; align-self:flex-start; border:1px solid #D8C286; border-radius:5px; background:#FFF9E9; color:#876315; padding:4px 8px; font-size:11px; }.composer-toggle.active { border-color:var(--line); background:transparent; color:var(--ink-soft); }.relation-composer { display:flex; flex-direction:column; gap:9px; margin-top:13px; }.relation-composer label { display:flex; flex-direction:column; gap:4px; color:var(--ink-soft); font-size:11px; }.relation-composer label small { color:#A3A69F; font-weight:400; }.relation-composer select, .relation-composer textarea { width:100%; border:1px solid var(--line); border-radius:6px; outline:0; background:#FFFEFC; color:var(--ink); font-size:12px; }.relation-composer select { height:33px; padding:0 8px; }.relation-composer textarea { resize:vertical; padding:7px 8px; line-height:1.55; }.relation-composer select:focus, .relation-composer textarea:focus { border-color:var(--gold); box-shadow:0 0 0 3px rgba(201,154,46,.1); }.composer-row { display:grid; grid-template-columns:1fr 1fr; gap:7px; }.composer-submit { width:100%; margin-top:2px; padding:8px 12px; font-size:12px; }
@keyframes loading-breathe { 50% { transform:scale(.7); opacity:.55; } }
@media (max-width:1100px) { .graph-summary { grid-template-columns:repeat(3, minmax(130px, 1fr)); }.summary-hint { grid-column:1 / -1; min-height:42px; }.graph-layout { grid-template-columns:1fr; }.inspector { max-height:none; }.graph-stage { min-height:480px; } }
@media (max-width:780px) { .graph-page { gap:13px; }.graph-page-head { align-items:flex-start; flex-direction:column; }.page-actions { width:100%; justify-content:flex-start; }.graph-summary { grid-template-columns:1fr 1fr; }.summary-card:last-of-type { grid-column:1 / -1; }.graph-toolbar { align-items:flex-start; flex-direction:column; }.legend { justify-content:flex-start; }.search-field { width:100%; }.filter-group { width:100%; }.select-field { flex:1; }.select-field select { width:100%; max-width:none; }.workspace-head > div:first-child span { display:none; }.graph-stage { min-height:420px; }.inspector { padding:15px; } }
</style>
