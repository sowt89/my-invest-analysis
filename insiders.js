/* SEC Form 4 P 보통주 매수. 기존 모멘텀 순위·22일 리밸런싱 계산은 별개다. */
let INSIDERS=null, INSIDER_STATE='loading', INSIDER_FILTER='active';
const insEsc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const insMoney=v=>v==null?'미확인':'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:0});
function insLink(url){
  try{const u=new URL(url);return u.protocol==='https:'&&u.hostname==='www.sec.gov'?insEsc(u.href):''}catch(e){return ''}
}
function insiderExpectedCutoff(now=new Date()){
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now).map(p=>[p.type,p.value]));
  const d=new Date(Date.UTC(Number(parts.year),Number(parts.month)-1,21));
  if(Number(parts.day)<21||(Number(parts.day)===21&&Number(parts.hour)*60+Number(parts.minute)<15*60+17))d.setUTCMonth(d.getUTCMonth()-1);
  return d.toISOString().slice(0,10);
}
function insiderDetail(ticker){
  if(MKT!=='us')return '';
  const c=INSIDERS?.companies.find(x=>x.ticker===ticker);
  let label=INSIDER_STATE==='error'?'내부자 매수 자료를 불러오지 못했습니다.':INSIDER_STATE==='loading'?'내부자 매수 자료를 불러오는 중입니다.':
    c?.active?`${insMoney(c.value)} · ${c.current_day_count}개 매수일 · ${c.buyer_count}명 · ${insEsc(c.pattern)}`:'해당 기간에 확인된 P 코드 매수가 없습니다.';
  if(INSIDERS&&!INSIDERS.coverage.complete)label+=' (일부 공시 확인 미완료)';
  if(INSIDERS?.review.some(r=>r.ticker===ticker))label+=' · 별도 확인이 필요한 공시가 있습니다.';
  return `<div class="d-card"><div class="d-h">내부자 매수 · SEC Form 4</div><p style="font-size:.84rem;line-height:1.7">${label}</p>${INSIDERS?`<p class="ins-meta">${insEsc(INSIDERS.window_start)}~${insEsc(INSIDERS.filing_cutoff||INSIDERS.as_of)} · 거래·공시일은 미국 날짜</p>`:''}<button type="button" class="ins-button" onclick="go('board');document.getElementById('insider-section').scrollIntoView({block:'start'})">월별 내부자 매수 목록 보기</button></div>`;
}
function insTransactions(rows){
  return `<div class="scroll-x"><table class="ins-table"><thead><tr><th>거래 / 공시일</th><th>매수자 · 직책</th><th>수량 × 단가</th><th>금액 / 근거</th></tr></thead><tbody>${rows.map(r=>`<tr>
    <td>${insEsc(r.date)}<small>${insEsc(r.filed)}</small></td>
    <td>${r.owners.map(o=>`${insEsc(o.name)}<small>${insEsc(o.role)}</small>`).join('<br>')}</td>
    <td>${r.shares==null?'미확인':Number(r.shares).toLocaleString('en-US')}<small>× ${r.price==null?'미확인':'$'+Number(r.price).toLocaleString('en-US',{maximumFractionDigits:4})}</small></td>
    <td>${insMoney(r.value)}<small><a href="${insLink(r.url)}" target="_blank" rel="noopener noreferrer">SEC 원문 ↗</a></small></td></tr>${r.flags.length||r.notes.length?`<tr><td colspan="4" class="ins-notes">${r.flags.map(insEsc).join(' · ')}${r.notes.length?`<details><summary>거래 성격·각주 확인</summary>${r.notes.map(n=>`<p>${insEsc(n)}</p>`).join('')}</details>`:''}</td></tr>`:''}`).join('')}</tbody></table></div>`;
}
function renderInsiders(){
  const section=document.getElementById('insider-section');
  if(!section)return;
  section.hidden=MKT!=='us';
  if(MKT!=='us')return;
  const body=document.getElementById('insider-body');
  if(!INSIDERS){
    body.innerHTML=`<p class="ins-meta">${INSIDER_STATE==='error'?'내부자 매수 자료를 불러오지 못했습니다. 미갱신 또는 연결 오류이며, 매수가 없다는 뜻은 아닙니다.':'SEC 내부자 매수 자료를 불러오는 중입니다.'}</p>`;
    return;
  }
  const d=INSIDERS, complete=d.coverage.complete;
  const stale=d.as_of<insiderExpectedCutoff();
  const own=new Set(STOCKS.filter(s=>s.th!=='지수 ETF').map(s=>s.t));
  const active=d.companies.filter(c=>c.active);
  const filtered=d.companies.filter(c=>INSIDER_FILTER==='history'?!c.active:c.active&&(INSIDER_FILTER==='repeat'?c.day_count>=2:INSIDER_FILTER==='own'?own.has(c.ticker):true));
  body.innerHTML=`
    <div class="ins-period"><b>${insEsc(d.as_of)} ${d.timezone==='Asia/Seoul'?'15:17 한국시간':'미국 날짜'} 기준</b><span>22일 매수 전 확인</span></div>
    <p class="ins-meta">매수 기간 ${insEsc(d.window_start)}~${insEsc(d.filing_cutoff||d.as_of)}<br>거래·공시일은 미국 날짜 · ${insEsc(d.filing_cutoff||d.as_of)}까지 공개된 공시 · 나스닥100 ${d.universe.tickers.length}개 종목</p>
    ${stale?'<p class="ins-warning">이번 기준일 자료가 아직 갱신되지 않았습니다. 아래는 이전 기준일 목록입니다.</p>':''}
    ${!complete?`<p class="ins-warning">일부 자료 확인 미완료 · 확인된 거래만 표시합니다. 누락된 종목이 있을 수 있습니다.</p>`:''}
    <div class="ins-totals"><span><b>${active.length}</b> 매수 기업</span><span><b>${active.filter(c=>c.day_count>=2).length}</b> 반복 매수 기업</span></div>
    <select id="insider-filter" aria-label="내부자 매수 필터" onchange="INSIDER_FILTER=this.value;renderInsiders()">
      <option value="active">기간 내 매수 전체</option><option value="repeat">반복 매수</option><option value="own">내 관심종목</option><option value="history">과거 매수만 있음</option>
    </select>
    <div class="ins-list">${filtered.length?filtered.map(c=>`<details class="ins-company"><summary>
      <div class="ins-row"><b>${insEsc(c.ticker)} ${own.has(c.ticker)?'<span class="ins-tag">관심종목</span>':''}</b><b>${insMoney(c.active?c.value:c.six_month_value)}</b></div>
      <div class="ins-name">${insEsc(c.name)}</div>
      <div class="ins-meta">${insEsc(c.pattern)} · 6개월 ${c.day_count}개 매수일 / ${c.month_count}개 월${c.active?` · 이번 기간 ${c.buyer_count}명`: ' · 금액은 6개월 합계'}</div>
      ${c.active&&(c.same_buyer_repeat||c.buyer_count>=2)?`<div class="ins-meta">${c.same_buyer_repeat?'동일 내부자 반복 매수 ':''}${c.buyer_count>=2?' · 여러 내부자 매수':''}</div>`:''}
      <span class="ins-expand">거래 내역·SEC 원문 보기</span></summary>
      <p class="ins-meta">최근 6개월: ${insEsc(d.lookback_start)}~${insEsc(d.filing_cutoff||d.as_of)} · 매수일 ${c.dates.map(insEsc).join(', ')}</p>
      ${insTransactions(c.transactions)}</details>`).join(''):`<p class="ins-meta ins-empty">${INSIDER_FILTER==='active'?'이 기간에 조건을 충족하는 P 코드 보통주 매수가 확인되지 않았습니다.':'이 필터에 해당하는 기업이 없습니다.'}${!complete?' 자료 확인이 끝난 결과는 아닙니다.':''}</p>`}</div>
    ${d.review.length?`<details class="ins-review"><summary>확인 필요 · 합계에서 제외한 거래 ${d.review.length}행</summary><p class="ins-meta">정정공시 또는 각주·주식 종류·단가를 대조해야 하는 거래입니다. 아래 내역은 최근 6개월 전체입니다.</p>${d.review.map(r=>`<p><b>${insEsc(r.ticker)}</b> · ${insEsc(r.name)}</p>${insTransactions([r])}`).join('')}</details>`:''}
    <details class="ins-method"><summary>선별 기준·확인 범위</summary><p class="ins-meta">P 코드 보통주 매수만 포함합니다. P에는 장내·사적 매수가 모두 포함되므로 거래 각주를 함께 확인하세요. 반복 매수는 최근 6개월의 서로 다른 매수일 2회 이상이며, 여러 달 반복과 단기 반복을 구분합니다. 동일 거래의 분할 체결은 매수일을 늘리지 않습니다. 여러 내부자 수에는 임원·이사·10% 이상 주주와 공동 신고자가 포함됩니다.<br>거래 출처: SEC 분기 데이터와 Form 4·4/A 원문. 지수 편입 이력: n100tickers(기존 사이트와 동일). 외국 발행인 등의 미신고 거래는 확인할 수 없습니다. 위 공시 마감일 이후 제출된 공시는 이 기준일 목록에 소급 반영하지 않습니다. 한국시간 매월 21일 15:17에 수집을 시작하며 실행 지연이 있을 수 있습니다.<br>마지막 수집 ${insEsc(d.generated_at)} · XML ${d.coverage.xml_count}건 확인. 기존 모멘텀 순위·매수 대상 5개를 변경하지 않는 참고 정보입니다.</p>
    ${d.coverage.errors.length||d.universe.missing_cik.length?`<p class="ins-warning">${insEsc(d.coverage.errors.join(' / '))}${d.universe.missing_cik.length?' · CIK 미확인: '+d.universe.missing_cik.map(insEsc).join(', '):''}</p>`:''}</details>`;
  document.getElementById('insider-filter').value=INSIDER_FILTER;
}
async function loadInsiders(){
  try{
    const r=await fetch('data/insider-purchases.json',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();
    if(d.schema_version!==1||!Array.isArray(d.companies)||!Array.isArray(d.review)||!d.coverage||!d.universe||!/^\d{4}-\d{2}-21$/.test(d.as_of))throw new Error('Invalid insider dataset');
    INSIDERS=d;INSIDER_STATE='ready';
  }catch(e){INSIDER_STATE='error';console.warn('SEC 내부자 매수 자료 로드 실패:',e.message)}
  renderInsiders();
  if(!document.getElementById('tab-detail').classList.contains('hidden'))renderDetail();
}
