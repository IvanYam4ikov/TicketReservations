const $ = id => document.getElementById(id);
const storageKey = 'front-row-session-v1';
let events = [], seats = [], selected = new Set(), active = null, order = null, attempt = null;
let busy = false, refreshing = false, eventId = null;
function save() {
  try { sessionStorage.setItem(storageKey, JSON.stringify({active, order, attempt, eventId})); }
  catch { message('Browser storage is unavailable. Keep this page open until checkout finishes.', true); }
}
function message(text, error = false) {
  $('message').textContent = text; $('message').hidden = !text;
  $('message').classList.toggle('error', error);
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {...options, signal: controller.signal});
    const data = await response.json();
    if (!response.ok) {
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
  $('reserve').disabled = busy || !selected.size;
  $('reserve').textContent = busy ? 'Reserving…' : 'Hold selected seats →';
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
  if (busy || !selected.size) return;
  busy=true; message(''); render();
  try {
    active=await api('/reservations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:eventId,seat_numbers:[...selected]})});
    selected.clear(); save(); message('Your seats are reserved for five minutes.');
  } catch(error) {
    message(error.status ? error.message : 'The reservation result is unknown. Refresh availability before trying again; an unconfirmed hold will expire within five minutes.',true);
  } finally {busy=false; await refresh(); render();}
};
$('checkout').onclick=async()=>{
  if(busy || !active) return;
  busy=true; message('');
  attempt ||= {key:crypto.randomUUID(),outcome:$('outcome').value}; save(); render();
  try {
    order=await api(`/reservations/${active.id}/checkout`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':attempt.key},body:JSON.stringify({payment_outcome:attempt.outcome})});
    attempt=null; save(); message('You’re all set. Your tickets are confirmed.');
  } catch(error) {
    if(error.status===402) {attempt=null; message('Payment declined. Your hold is still subject to its original deadline. Select Approve payment to try again.',true);}
    else if(error.status && error.status<500) {attempt=null; message(error.message,true);}
    else message('Payment result is unconfirmed. Retry same payment to safely recover the result.',true);
    save();
  } finally {busy=false; await refresh(); render();}
};
$('start-over').onclick=()=>{active=null;order=null;attempt=null;selected.clear();save();message('');render();refresh();};
async function init() {
  try {
    events=await api('/events');
    $('event').replaceChildren(...events.map(e=>{const option=document.createElement('option');option.value=e.id;option.textContent=e.name;return option;}));
    try {
      const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');
      if(saved && events.some(e=>e.id===saved.eventId)) ({active,order,attempt,eventId}=saved);
    } catch { /* A missing or malformed saved session starts fresh. */ }
    eventId ||= events[0]?.id; $('event').value=eventId || '';
    if(attempt) $('outcome').value=attempt.outcome;
    if(order) order=await api(`/orders/${order.id}`);
    else if(active) {
      try {
        const latest=await api(`/reservations/${active.id}`);
        active={...active,expires_at:latest.expires_at};
      } catch(error) {if(error.status===404){active=null;attempt=null;save();}else throw error;}
    }
    render(); await refresh();
  } catch {message('Could not load your session. Check that the server is running and reload this page.',true);}
}
setInterval(tick,1000);
setInterval(()=>{if(!busy && !document.hidden) refresh();},5000);
init();
