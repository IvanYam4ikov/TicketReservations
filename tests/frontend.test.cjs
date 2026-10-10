// Lightweight state tests; no browser or DOM rendering is exercised here.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {webcrypto} = require('node:crypto');

async function setup() {
  const elements = new Map();
  function element() { return {value:'succeeded',dataset:{},classList:{toggle(){}},
    replaceChildren(...children){this.children=children;},append(child){(this.children ||= []).push(child);},
    setAttribute(){},querySelector(){return null;},focus(){}}; }
  const get = id => {if(!elements.has(id)) elements.set(id,element()); return elements.get(id);};
  const calls=[]; let failure=false, payment='succeeded'; const stored=new Map();
  const context=vm.createContext({document:{getElementById:get,createElement:element,hidden:false},
    sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)},
    AbortController,Intl,Date,Set,JSON,Math,String,Error,crypto:webcrypto,
    setTimeout,clearTimeout,setInterval(){},
    fetch:async(path,opts={})=>{
      calls.push({path,opts});let data, status=200;
      if(path==='/events') data=[{id:'event',name:'Test event',currency:'USD',starts_at:'2030-06-01T19:00:00Z'}];
      else if(path.endsWith('/seats')) data=[{seat_number:1,price_cents:7500,status:'available'}];
      else if(path==='/reservations') data={id:'hold',event_id:'event',seat_numbers:[1],expires_at:new Date(Date.now()+300000).toISOString(),total_price_cents:7500};
      else if(path.endsWith('/checkout')) {
        if(failure) throw new Error('network lost');
        if(payment==='declined'){status=402;data={detail:'Simulated payment declined'};}
        else data={id:'order',total_price_cents:7500,tickets:[{id:'ticket',seat_number:1}]};
      } else throw new Error('Unexpected request '+path);
      return {ok:status<400,status,json:async()=>data};
    }});
  vm.runInContext(fs.readFileSync('ticketing/static/app.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return {get,calls,context,setFailure:v=>failure=v,setPayment:v=>payment=v};
}
async function reserve(env) {
  env.get('seats').children[0].onclick();
  assert.equal(env.get('reserve').disabled,false);
  await env.get('reserve').onclick();
  assert.equal(env.get('checkout-panel').hidden,false);
}
test('selection, reservation, checkout, and receipt',async()=>{
  const env=await setup(); await reserve(env);
  await env.get('checkout').onclick();
  assert.equal(env.get('summary-title').textContent,'You’re going.');
  assert.equal(env.get('receipt').hidden,false);
  assert.equal(env.get('total').textContent,'$75.00');
});
test('unknown payment retries preserve key and outcome',async()=>{
  const env=await setup();await reserve(env);env.setFailure(true);
  await env.get('checkout').onclick();
  assert.equal(env.get('outcome').disabled,true);
  assert.equal(env.get('checkout').textContent,'Retry same payment →');
  env.setFailure(false);await env.get('checkout').onclick();
  const calls=env.calls.filter(c=>c.path.endsWith('/checkout'));
  assert.equal(calls[0].opts.headers['Idempotency-Key'],calls[1].opts.headers['Idempotency-Key']);
  assert.equal(calls[0].opts.body,calls[1].opts.body);
});
test('decline allows a new payment attempt with a new key',async()=>{
  const env=await setup();await reserve(env);env.setPayment('declined');env.get('outcome').value='declined';
  await env.get('checkout').onclick();assert.equal(env.get('outcome').disabled,false);
  env.setPayment('succeeded');env.get('outcome').value='succeeded';await env.get('checkout').onclick();
  const calls=env.calls.filter(c=>c.path.endsWith('/checkout'));
  assert.notEqual(calls[0].opts.headers['Idempotency-Key'],calls[1].opts.headers['Idempotency-Key']);
});
test('expired hold blocks fresh checkout but permits uncertain retry',async()=>{
  const env=await setup();await reserve(env);
  vm.runInContext("active.expires_at='2000-01-01T00:00:00Z'; tick()",env.context);
  assert.equal(env.get('checkout').disabled,true);
  assert.equal(env.get('start-over').hidden,false);
  vm.runInContext("attempt={key:'pending',outcome:'succeeded'}; tick()",env.context);
  assert.equal(env.get('checkout').disabled,false);
  assert.equal(env.get('start-over').hidden,true);
});
