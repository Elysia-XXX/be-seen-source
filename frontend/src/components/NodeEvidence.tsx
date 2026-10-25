import {ArrowUpRight,FileText,Search,Store,X} from 'lucide-react'
import {useEffect,useRef} from 'react'
import type {ChatReport,GraphData,GraphNode,GraphLink} from '../chat-types'
import {statusNames} from '../chat-types'
import type {Page} from '../types'
import {Source} from './Evidence'

const end=(x:string|GraphNode)=>typeof x==='string'?x:x.id
const kinds={query:{label:'需求查询',Icon:Search},source:{label:'来源页面',Icon:FileText},business:{label:'商家名称',Icon:Store}}
export default function NodeEvidence({selection,graph,report,pages,onSelect,onClose,onAsk,onResearch}:{selection:GraphNode|GraphLink;graph:GraphData;report:ChatReport;pages:Page[];onSelect:(n:GraphNode)=>void;onClose:()=>void;onAsk?:()=>void;onResearch?:(query:string,ids:string[],title?:string)=>void}){
 const node='type' in selection?selection:null,link='relation' in selection?selection:null
 const drawer=useRef<HTMLElement>(null)
 useEffect(()=>{if(drawer.current)drawer.current.scrollTop=0},[node?.id,link])
 const nodes=new Map(graph.nodes.map(n=>[n.id,n])),byId=new Map(pages.map(p=>[p.id,p]))
 const related=graph.links.filter(l=>node&&(end(l.source)===node.id||end(l.target)===node.id))
 const evidenceIds=[...new Set(link?link.evidence_ids:node?.type==='source'?(node.evidence_ids||[node.id]):related.flatMap(l=>l.evidence_ids))]
 const evidence=evidenceIds.map(id=>byId.get(id)).filter((p):p is Page=>!!p)
 const queryIds=new Set(graph.links.filter(l=>l.evidence_ids.some(id=>evidenceIds.includes(id))&&nodes.get(end(l.source))?.type==='query').map(l=>end(l.source)))
 related.forEach(l=>{if(nodes.get(end(l.source))?.type==='query')queryIds.add(end(l.source))})
 const queries=graph.nodes.filter(n=>queryIds.has(n.id))
 const businessIds=new Set(graph.links.filter(l=>l.evidence_ids.some(id=>evidenceIds.includes(id))&&nodes.get(end(l.target))?.type==='business').map(l=>end(l.target)))
 const businesses=graph.nodes.filter(n=>businessIds.has(n.id))
 const scene=node?.type==='query'?report.scenarios.find(s=>'query:'+s.id===node.id):undefined
 const sourceEvidence=scene?.reading_ids?.map(id=>byId.get(id)).filter((p):p is Page=>!!p)||evidence
 const meta=node?kinds[node.type]:null
 const angles=[...new Set(report.scenarios.flatMap(s=>s.content_clues||[]).filter(c=>evidenceIds.includes(c.evidence_id)).flatMap(c=>c.angles))]
 function jump(n:GraphNode){onSelect({...n})}
 function queryList(){return <section className="node-query-paths"><h3>在哪些查询下出现</h3>{queries.map(n=><button key={n.id} className="node-path" onClick={()=>jump(n)}><Search size={14}/><span>{n.query||n.label}</span><ArrowUpRight size={14}/></button>)}{!queries.length&&<p>此来源来自补充证据，未记录关联查询。</p>}</section>}
 function merchantList(){return businesses.length>0&&<section><h3>结果提及的名称</h3><div className="node-merchant-list">{businesses.map(n=><button key={n.id} onClick={()=>jump(n)}><Store size={14}/>{n.label}<ArrowUpRight size={12}/></button>)}</div></section>}
 function sourceList(){return <section className="node-source-results"><h3>{node?.type==='query'?'这次搜索返回的页面':'支持提及的来源'}</h3>{node?.type==='query'&&scene?.reading_ids&&<p>优先展示文章线索，保留原搜索结果位置。</p>}{sourceEvidence.map(p=><div key={p.id}><button className="node-source-jump" onClick={()=>{const n=graph.nodes.find(n=>n.type==='source'&&(n.evidence_ids||[n.id]).includes(p.id));if(n)jump(n)}}><FileText size={14}/>在图中定位此来源<ArrowUpRight size={14}/></button><Source page={p} quote={node?.type==='business'?related.find(l=>l.evidence_ids.includes(p.id))?.quote:undefined}/></div>)}{!evidence.length&&<p>没有已保存的来源页面。</p>}</section>}
 return <aside ref={drawer} className={'evidence-drawer node-panel node-panel-'+(node?.type||'relation')} aria-label="所选节点与证据" data-node-type={node?.type||'relation'}>
  <div className="drawer-heading"><span className="node-type-label">{meta&&<meta.Icon size={16}/>} {meta?.label||'关系证据'}</span><button className="icon-button" aria-label="关闭证据详情" onClick={onClose}><X size={18}/></button></div>
  <h2>{node?.query||node?.label||link?.relation}</h2>
  {node?.type==='query'&&<>
   <div className="node-facts"><span>{({brand:'品牌资料 · 不计自然曝光',competitor:'竞品追查',content:'文章补充 · 不计初始曝光',followup:'补充搜索'} as Record<string,string>)[node.search_kind||'']||'需求检索'}</span><span>{statusNames[node.status||'']||'已记录查询'}</span><span>{evidence.length} 条结果</span></div>
   {scene?.queried_at&&<p className="node-timestamp">检索于 {new Date(scene.queried_at).toLocaleString('zh-CN')}</p>}
   {!!scene?.mentions?.length&&<p className="node-observation">目标名称出现在第 {scene.mentions.map(m=>m.rank).join('、')} 条结果。名称提及与门店身份分别记录。</p>}
   {scene?.errors.map((e,i)=><p className="error" key={i}>{e}</p>)}
   {onResearch&&<button className="button secondary" onClick={()=>onResearch(node.query||node.label,evidenceIds)}>分析此查询的宣传内容<ArrowUpRight size={15}/></button>}
   {merchantList()}{sourceList()}
   {!!scene?.recommendation.recommendations?.length&&<section><h3>本次模拟推荐</h3>{scene.recommendation.recommendations.map((r,i)=><p key={i}><strong>{r.name}</strong>：{r.reason}</p>)}</section>}
   {scene?.recommendation.abstention_reason&&<p>{scene.recommendation.abstention_reason}</p>}
   {scene?.request_id&&<details className="node-audit"><summary>查询记录</summary><p className="request-id">{scene.request_id}</p></details>}
  </>}
  {node?.type==='source'&&<>
   <p className="node-context-note">搜索摘要与原站正文分开保存；同一页面在不同查询中的记录列于下方。</p>
   {evidence[0]&&<Source page={evidence.find(p=>p.article?.status==='read')||evidence[0]}/>}
   {evidence.length>1&&<details className="source-occurrences"><summary>查看 {evidence.length} 次检索中的摘要与位置</summary>{evidence.map(p=><Source key={p.id} page={p}/>)}</details>}
   {!!angles.length&&<section><h3>这篇内容的宣传线索</h3>{angles.map((a,i)=><blockquote key={i}>{a}</blockquote>)}</section>}
   {queryList()}{merchantList()}
   {onResearch&&<button className="button secondary" onClick={()=>onResearch(queries[0]?.query||queries[0]?.label||'补充来源',evidenceIds,node.label)}>追查这篇内容<ArrowUpRight size={15}/></button>}
  </>}
  {node?.type==='business'&&<>
   <div className="node-facts"><span>{node.is_target?'目标名称':'结果中的商家名称'}</span><span>{new Set(evidence.map(p=>p.canonical_url||p.id)).size} 个来源</span><span>{queries.length} 条关联查询</span></div>
   <p className="node-context-note">这里汇总名称的出现路径，来源中的提及不自动代表同一家门店。</p>
   {queryList()}
   {related.filter(l=>l.quote).length>0&&<section><h3>原文如何提到它</h3>{[...new Set(related.map(l=>l.quote).filter(Boolean))].map((q,i)=><blockquote key={i}>{q}</blockquote>)}</section>}
   {sourceList()}
   {onResearch&&<button className="button secondary" onClick={()=>onResearch(queries[0]?.query||queries[0]?.label||node.label,evidenceIds,node.label+'的公开宣传内容')}>研究这个名称的宣传内容<ArrowUpRight size={15}/></button>}
  </>}
  {link&&<><p>{link.reason||'这条连接来自报告保存的检索或引用记录。'}</p>{link.rank&&<p>该查询下第 {link.rank} 条返回结果</p>}{link.quote&&<blockquote>{link.quote}</blockquote>}<div className="node-merchant-list">{[end(link.source),end(link.target)].map(id=>{const n=nodes.get(id);return n&&<button key={id} onClick={()=>jump(n)}>定位{n.type==='query'?'查询':n.type==='source'?'来源':'名称'}：{n.label}<ArrowUpRight size={13}/></button>})}</div>{evidence.map(p=><Source key={p.id} page={p}/>)}</>}
  {onAsk&&<button className="button primary continue-question" onClick={onAsk}>围绕此证据继续问<ArrowUpRight size={16}/></button>}
 </aside>
}
