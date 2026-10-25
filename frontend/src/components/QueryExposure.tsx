import {ArrowUpRight,Search} from 'lucide-react'
import type {ChatReport} from '../chat-types'
import type {SceneResult,Page} from '../types'
const host=(url:string)=>{try{return new URL(url).hostname}catch{return '原始来源'}}

export default function QueryExposure({report,onScene,onResearch}:{report:ChatReport;onScene:(id:string)=>void;onResearch?:(query:string,ids:string[],title?:string)=>void}){
 const demands=report.scenarios.filter(s=>s.search_kind!=='brand'&&s.search_kind!=='content')
 const investigations=report.scenarios.filter(s=>s.search_kind==='content')
 const brands=report.scenarios.filter(s=>s.search_kind==='brand')
 const summary=report.exposure
 function row(s:SceneResult){
  const ordered=s.reading_ids?.map(id=>s.pages.find(p=>p.id===id)).filter((p):p is Page=>!!p)||s.pages
  const articles=ordered.filter(p=>p.reading_hint?.kind==='article')
  const preview=(articles.length?articles:ordered).slice(0,3),remaining=ordered.filter(p=>!preview.some(v=>v.id===p.id))
  function pageRow(p:Page){return <div className="query-source" key={p.id}><span className="source-position" title="原搜索结果位置">{String(p.rank).padStart(2,'0')}</span><div><a href={/^https?:\/\//.test(p.url)?p.url:undefined} target="_blank" rel="noreferrer">{p.title||p.url}<ArrowUpRight size={13}/></a><p>{(p.passage||p.content||'搜索未返回摘要。').slice(0,300)}</p><span>{p.article?.status==='read'?'已读取正文片段 · ':p.article?'正文未读取 · ':''}{p.site||host(p.url)}{p.reading_hint?' · '+p.reading_hint.label:''} · 原结果第 {p.rank} 条</span>{onResearch&&<button className="text-button read-article" onClick={()=>onResearch(s.query,[p.id],p.title)}>解读这篇内容 <ArrowUpRight size={13}/></button>}</div></div>}
  return <article className="exposure-query" key={s.id}>
  <div className="exposure-query-heading"><div><span className="query-group">{s.group}</span><h3>{s.query}</h3></div><span className={'status-tag '+s.mention_status}>{!s.search_ok?'搜索未完成':s.mentions?.length?'名称出现':'未见目标名称'}</span></div>
  <div className="exposure-query-meta"><span>返回 {s.pages.length} 条</span><span>{s.mention_first_rank?`名称首次出现在第 ${s.mention_first_rank} 条`:'本次标题和摘要的可见提及'}</span><button className="text-button" onClick={()=>onScene(s.id)}>查看全部结果</button></div>
  {s.parent_query&&<p className="reading-note">为“{s.parent_query}”补充文章线索，不计入初始需求查询次数。</p>}
  {!!s.mentions?.length&&<p className="mention-note">{s.mentions.map(m=>`第 ${m.rank} 条：${m.name}`).join('；')}。这是名称出现记录，身份疑点不会抹去这次提及。</p>}
  {!!ordered.length&&<div className="query-source-list"><h4>优先阅读文章与体验分享</h4><p className="reading-note">按标题和链接中的内容线索排序，数字保留原搜索位置；内容类型是阅读提示；正文读取状态另列。</p>{preview.map(pageRow)}{remaining.length>0&&<details className="more-reading"><summary>展开其余 {remaining.length} 条内容与目录结果</summary>{remaining.map(pageRow)}</details>}</div>}
  {!!s.content_clues?.length&&<div className="content-clues"><h4>结果中的宣传线索</h4>{s.content_clues.map((c,i)=>{const p=s.pages.find(p=>p.id===c.evidence_id);return <div className="content-clue" key={c.evidence_id+'-'+i}><span>第 {p?.rank??'—'} 条结果{c.merchants.length?' · '+c.merchants.join('、'):''}</span>{c.angles.map((a,j)=><blockquote key={j}>{a}</blockquote>)}{!c.angles.length&&<p>出现的商家名称可作为进一步内容检索的线索。</p>}{onResearch&&<button className="text-button" onClick={()=>onResearch(s.query,[c.evidence_id],p?.title)}>分析这篇内容 <ArrowUpRight size={13}/></button>}</div>})}</div>}
  {!s.content_clues?.length&&s.pages.length>0&&<p className="muted">结果已保留，可以继续分析页面中的卖点、探店内容或宣传表达。</p>}
  {!s.pages.length&&<p className="muted">{s.search_ok?'本次未返回结果，可以换一个标签或消费场景继续搜。':'此次查询未完成，不能当作没有曝光。'}</p>}
  {onResearch&&<button className="button secondary research-query" onClick={()=>onResearch(s.query,s.pages.slice(0,10).map(p=>p.id))}><Search size={15}/>追查这条查询下的宣传内容</button>}
 </article>}
 return <section className="exposure-overview"><header><h2>顾客这样搜，结果里有你吗？</h2><p>先看每个需求下的名称提及，再看哪些页面和宣传表达值得研究。</p></header>
 {summary&&<div className="exposure-summary"><strong>{summary.successful_queries?<>{summary.mentioned_queries}<span> / {summary.successful_queries}</span></>:'暂无法判断'}</strong><div><b>成功的需求查询中，出现目标名称的次数</b><p>{summary.failed_queries?`${summary.failed_queries} 次未完成，未计入。`:'失败查询不计入；空结果计为未出现。'} 品牌补充搜索另列。</p></div></div>}
 <p className="muted">{summary?.basis||'逐条呈现本轮检索结果，不代表全网知名度。'} 页面位置不是商家排名，内容线索不等于付费广告。</p>
 {demands.map(row)}
 {!!investigations.length&&<section className="article-investigations"><h3>沿着信息缺口追查文章</h3><p className="muted">以下查询用于研究内容表达，单独保留，不计入上方需求曝光。</p>{investigations.map(row)}</section>}
 {!!brands.length&&<details className="brand-research"><summary>品牌补充资料 · 不计自然曝光（{brands.length} 次查询）</summary>{brands.map(row)}</details>}
 </section>
}
