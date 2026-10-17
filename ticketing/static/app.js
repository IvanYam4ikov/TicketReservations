const $ = id => document.getElementById(id);
const storageKey = 'front-row-account-v2:';
let user = null, accountVersion = 0, authBusy = false, registerMode = false;
let historyKind = 'orders', historyCursor = null, historyBusy = false, historyVersion = 0;
let events = [], seats = [], selected = new Set(), active = null, order = null, attempt = null;
let busy = false, refreshing = false, eventId = null;
function save() {
  if (!user) return;
  try { sessionStorage.setItem(storageKey + user.id, JSON.stringify({active, order, attempt, eventId})); }
  catch { message('Browser storage is unavailable. Keep this page open until checkout finishes.', true); }
}
function message(text, error = false) {
  $('message').textContent = text; $('message').hidden = !text;
  $('message').classList.toggle('error', error);
}
async function api(path, options = {}) {
  const version = accountVersion;
  const protectedPath = !path.startsWith('/auth/') && (path.startsWith('/me/') || path.startsWith('/orders/') || path.startsWith('/reservations'));
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const headers = {...options.headers};
    if (options.method && options.method !== 'GET') headers['X-Requested-With'] = 'FrontRow';
    if (protectedPath && user) headers['X-Account-ID'] = user.id;
    const response = await fetch(path, {...options, headers, credentials:'same-origin', signal: controller.signal});
    const data = await response.json();
    if (protectedPath && version !== accountVersion) throw new Error('Account changed. Please try again.');
    if (!response.ok) {
      if (protectedPath && (response.status === 401 || data.detail === 'Account changed. Reload before continuing')) setAccount(null);
      const error = new Error(typeof data.detail === 'string' ? data.detail : 'Please check your request and try again.');
      error.status = response.status; throw error;
    }
    return data;
  } finally { clearTimeout(timeout); }
}
function money(cents) {
  return new Intl.NumberFormat(undefined, {style:'currency', currency: events.find(e => e.id === eventId)?.currency || 'USD'}).format(cents / 100);
}
function remaining() { return active ? Math.max(0, Math.ceil((Date.parse(active.expires_at) - Date.now()) / 1000)) : 0; }
function render() {
  $('account-email').textContent = user?.email || '';
  $('account-toggle').hidden = !!user;
  $('tickets-toggle').hidden = !user;
  $('logout').hidden = !user;
  for (const id of ['logout','account-toggle','tickets-toggle']) $(id).disabled = busy || authBusy;
  $('auth-submit').disabled = busy || authBusy;
  $('auth-switch').disabled = busy || authBusy;
  const event = events.find(e => e.id === eventId);
  $('event-title').textContent = event?.name || 'No events available';
  $('event-date').textContent = event ? new Date(event.starts_at).toLocaleString(undefined, {dateStyle:'full', timeStyle:'short'}) : 'Check back soon for upcoming events.';
  $('event').disabled = busy || !!active || !!order || !events.length;
  const focusedSeat = document.activeElement?.dataset.seat;
  $('seats').replaceChildren();
  seats.forEach(s => {
    const mine = active && remaining() > 0 && active.seat_numbers.includes(s.seat_number) && s.status === 'held';
    const button = document.createElement('button');
    const picked = selected.has(s.seat_number) || mine;
    button.className = `seat ${picked ? 'selected' : s.status}`;
    button.textContent = s.seat_number; button.dataset.seat = s.seat_number;
    button.setAttribute('aria-label', `Seat ${s.seat_number}, ${mine ? 'your hold' : picked ? 'selected' : s.status}, ${money(s.price_cents)}`);
    button.setAttribute('aria-pressed', String(picked));
    button.disabled = busy || !!active || !!order || s.status !== 'available';
    button.onclick = () => {
      if (selected.has(s.seat_number)) selected.delete(s.seat_number);
      else if (selected.size < 10) selected.add(s.seat_number);
      else { message('You can reserve up to 10 seats at a time.', true); return; }
      message(''); render();
    };
    $('seats').append(button);
  });
  if (focusedSeat) $('seats').querySelector(`[data-seat="${focusedSeat}"]`)?.focus();
  const numbers = order ? order.tickets.map(t => t.seat_number) : active ? active.seat_numbers : [...selected].sort((a,b)=>a-b);
  $('chosen').replaceChildren(...numbers.map(n => { const span = document.createElement('span'); span.className='chip'; span.textContent=`Seat ${n}`; return span; }));
  const total = order?.total_price_cents ?? active?.total_price_cents ?? seats.filter(s=>selected.has(s.seat_number)).reduce((sum,s)=>sum+s.price_cents,0);
  $('total').textContent = numbers.length ? money(total) : '—';
  $('summary-title').textContent = order ? 'You’re going.' : active ? remaining() ? 'Your seats are held.' : 'Your hold expired.' : 'Make it yours.';
  $('summary-help').textContent = order ? 'Your tickets are confirmed. See you there.' : active ? 'Complete your simulated purchase below.' : 'Select seats on the map to get started.';
  $('reserve').hidden = !!active || !!order;
  $('reserve').disabled = busy || authBusy || !selected.size;
  $('reserve').textContent = busy ? 'Reserving…' : user ? 'Hold selected seats →' : 'Sign in to reserve →';
  $('checkout-panel').hidden = !active || !!order;
  $('countdown-box').hidden = !active || !!order;
  $('outcome').disabled = busy || !!attempt;
  $('checkout').disabled = busy || (!remaining() && !attempt);
  $('checkout').textContent = busy ? 'Processing…' : attempt ? 'Retry same payment →' : 'Complete purchase →';
  $('start-over').hidden = busy || !!attempt || (!order && (!active || remaining() > 0));
  $('receipt').hidden = !order;
  $('receipt').replaceChildren();
  if (order) {
    const heading = document.createElement('strong'); heading.textContent='Order confirmed'; $('receipt').append(heading);
    const id = document.createElement('p'); id.textContent=`Order ${order.id}`; $('receipt').append(id);
    order.tickets.forEach(t => { const p=document.createElement('p'); p.textContent=`Seat ${t.seat_number} · Ticket ${t.id}`; $('receipt').append(p); });
  }
  tick();
}
function tick() {
  const seconds=remaining(); $('countdown').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  if (active && !seconds && !order) {
    $('summary-title').textContent='Your hold expired.';
    $('summary-help').textContent=attempt ? 'A payment result is unconfirmed. Retry with the same key to recover it.' : 'Choose more seats to place a new hold.';
    $('checkout').disabled=busy || !attempt;
    $('start-over').hidden=busy || !!attempt;
  }
}
async function refresh() {
  if (!eventId || refreshing) return;
  refreshing=true; const requestedEvent=eventId;
  try {
    const latest=await api(`/events/${requestedEvent}/seats`);
    if (eventId !== requestedEvent) return;
    seats=latest;
    const removed=[...selected].some(n=>!seats.some(s=>s.seat_number===n && s.status==='available'));
    selected=new Set([...selected].filter(n=>seats.some(s=>s.seat_number===n && s.status==='available')));
    if (removed && !active && !order) message('Some selected seats are no longer available. Please check your selection.',true);
    render();
  } catch { message('Could not refresh availability. Check your connection and use Refresh availability.',true); }
  finally { refreshing=false; }
}
$('event').onchange=async()=>{eventId=$('event').value; selected.clear(); seats=[]; render(); await refresh(); save();};
$('refresh').onclick=refresh;
$('reserve').onclick=async()=>{
  if (busy || authBusy || !selected.size) return;
  if (!user) { showAuth(); return; }
  busy=true; message(''); render();
  try {
    active=await api('/reservations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:eventId,seat_numbers:[...selected]})});
    selected.clear(); save(); message('Your seats are reserved for five minutes.');
  } catch(error) {
    message(error.status ? error.message : 'The reservation result is unknown. Refresh availability before trying again; an unconfirmed hold will expire within five minutes.',true);
  } finally {busy=false; await refresh(); render();}
};
$('checkout').onclick=async()=>{
  if(busy || !active || !user || authBusy) return;
  busy=true; message('');
  attempt ||= {key:crypto.randomUUID(),outcome:$('outcome').value}; save(); render();
  try {
    order=await api(`/reservations/${active.id}/checkout`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':attempt.key},body:JSON.stringify({payment_outcome:attempt.outcome})});
    attempt=null; save(); message('You’re all set. Your tickets are confirmed.');
  } catch(error) {
    if(error.status===402) {attempt=null; message('Payment declined. Your hold is still subject to its original deadline. Select Approve payment to try again.',true);}
    else if(error.status && error.status<500) {attempt=null; message(error.message,true);}
    else message('Payment result is unconfirmed. Retry same payment to safely recover the result.',true);
    if(user) save();
  } finally {busy=false; await refresh(); render();}
};
$('start-over').onclick=()=>{active=null;order=null;attempt=null;selected.clear();save();message('');render();refresh();};
function setAccount(next) {
  if (user?.id === next?.id) { user = next; return; }
  save(); user=next; accountVersion++; historyVersion++;
  active=null; order=null; attempt=null; selected.clear();
  historyCursor=null; historyBusy=false;
  $('history-list').replaceChildren(); $('history-status').textContent='';
  $('history-panel').hidden=true; $('history-more').hidden=true;
  $('auth-password').value='';
  if(user) $('auth-panel').hidden=true;
  render();
}
function showAuth() {
  $('auth-panel').hidden=false; $('auth-email').focus();
}
async function restoreSession() {
  if(!user) return;
  const version=accountVersion;
  try {
    const saved=JSON.parse(sessionStorage.getItem(storageKey+user.id)||'null');
    if(!saved || !events.some(e=>e.id===saved.eventId)) return;
    if(saved.order) {
      const restored=await api(`/orders/${saved.order.id}`);
      if(version!==accountVersion) return;
      order=restored; active=null; attempt=null;
    } else if(saved.active) {
      const restored=await api(`/reservations/${saved.active.id}`);
      if(version!==accountVersion) return;
      active={...saved.active,expires_at:restored.expires_at}; attempt=saved.attempt;
      if(attempt) $('outcome').value=attempt.outcome;
      // A completed checkout with a lost response can be recovered via its key or My Tickets.
    }
    if(version!==accountVersion) return;
    eventId=saved.eventId; $('event').value=eventId; save();
  } catch(error) {
    if(error.status===404) {active=null;order=null;attempt=null;save();}
    else if(error.status!==401) message('Could not restore the booking. Open My Tickets or Reservations to recover it.',true);
  }
  render();
}
async function syncAccount() {
  const version=accountVersion;
  try {
    const next=await api('/auth/me');
    if(version!==accountVersion || authBusy || busy) return;
    if(next.id!==user?.id) {setAccount(next); await restoreSession();}
  } catch(error) {if(error.status===401) {if(version===accountVersion && !authBusy && !busy) setAccount(null);} else throw error;}
}
$('account-toggle').onclick=showAuth;
$('auth-switch').onclick=()=>{
  registerMode=!registerMode;
  $('auth-title').textContent=registerMode?'Create your account.':'Sign in.';
  $('auth-submit').textContent=registerMode?'Create account':'Sign in';
  $('auth-switch').textContent=registerMode?'Already registered? Sign in':'New here? Create an account';
  $('auth-password').setAttribute('autocomplete',registerMode?'new-password':'current-password');
  $('auth-error').textContent='';
};
$('auth-form').onsubmit=async event=>{
  event.preventDefault(); if(authBusy || busy) return;
  authBusy=true; $('auth-error').textContent=''; render();
  try {
    const next=await api(registerMode?'/auth/register':'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:$('auth-email').value,password:$('auth-password').value})});
    setAccount(next); await restoreSession(); message('Signed in. Your tickets are saved to your account.'); await refresh();
  } catch(error) {$('auth-error').textContent=error.status?error.message:'Could not sign in. Check your connection and try again.';}
  finally {authBusy=false; $('auth-password').value=''; render();}
};
$('logout').onclick=async()=>{
  if(busy || authBusy) return;
  authBusy=true; render();
  try {await api('/auth/logout',{method:'POST'}); setAccount(null); message('Signed out. Your purchases are saved in My Tickets.');}
  catch {message('Could not confirm sign-out. Please try again.',true);}
  finally {authBusy=false;render();}
};
function historyCard(item) {
  const card=document.createElement('article'); card.className='history-card';
  const heading=document.createElement('h3'); heading.textContent=item.event_name; card.append(heading);
  const summary=document.createElement('p');
  summary.textContent=`${new Date(item.starts_at).toLocaleString()} · ${item.status || 'confirmed'} · ${new Intl.NumberFormat(undefined,{style:'currency',currency:item.currency}).format(item.total_price_cents/100)}`; card.append(summary);
  const id=document.createElement('p');id.textContent=`${historyKind==='orders'?'Order':'Reservation'} ${item.id}`;card.append(id);
  (item.tickets || item.items).forEach(ticket=>{const p=document.createElement('p');p.textContent=`Seat ${ticket.seat_number}${ticket.id?' · Ticket '+ticket.id:''}`;card.append(p);});
  if(historyKind==='reservations' && item.status==='held') {
    const button=document.createElement('button');button.className='text-button';button.textContent='Continue checkout →';
    button.onclick=async()=>{
      if(busy || authBusy || !user) return;
      if(attempt && active?.id!==item.id) {message('Resolve your pending payment before switching reservations.',true);return;}
      const pending=active?.id===item.id?attempt:null;
      active={...item,seat_numbers:item.items.map(i=>i.seat_number)};order=null;attempt=pending;
      selected.clear();eventId=item.event_id;$('event').value=eventId;
      save();$('history-panel').hidden=true;render();await refresh();
    };card.append(button);
  }
  return card;
}
async function loadHistory(reset=true) {
  if(!user || busy || authBusy || (historyBusy && !reset)) return;
  const version=++historyVersion; historyBusy=true;
  if(reset){historyCursor=null;$('history-list').replaceChildren();}
  $('history-more').disabled=true;$('history-status').textContent='Loading…';
  try {
    const result=await api(`/me/${historyKind}?limit=10${historyCursor?'&cursor='+encodeURIComponent(historyCursor):''}`);
    if(version!==historyVersion || !user) return;
    result.items.forEach(item=>$('history-list').append(historyCard(item)));
    historyCursor=result.next_cursor;$('history-more').hidden=!historyCursor;
    $('history-status').textContent=reset && !result.items.length ? 'Nothing here yet. Your next night starts with a seat.' : '';
  } catch(error) {if(version===historyVersion) $('history-status').textContent=error.status?error.message:'Could not load history. Use Refresh to retry.';}
  finally {if(version===historyVersion){historyBusy=false;$('history-more').disabled=false;}}
}
$('tickets-toggle').onclick=()=>{if(user){$('history-panel').hidden=false;loadHistory();}};
$('history-close').onclick=()=>{$('history-panel').hidden=true;};
$('history-refresh').onclick=()=>loadHistory();
$('history-more').onclick=()=>loadHistory(false);
for(const kind of ['orders','reservations']) $('history-'+kind).onclick=()=>{
  historyKind=kind;
  for(const tab of ['orders','reservations']) $('history-'+tab).setAttribute('aria-pressed',String(tab===kind));
  loadHistory();
};
async function init() {
  try {
    events=await api('/events');
    $('event').replaceChildren(...events.map(e=>{const option=document.createElement('option');option.value=e.id;option.textContent=e.name;return option;}));
    eventId=events[0]?.id; $('event').value=eventId || '';
    await syncAccount(); render(); await refresh();
  } catch {message('Could not load your session. Check that the server is running and reload this page.',true);}
}
setInterval(tick,1000);
setInterval(async()=>{if(!busy && !authBusy && !document.hidden) {try {await syncAccount();} catch {} refresh();}},5000);
init();
