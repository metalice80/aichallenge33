'use strict';
const $ = id => document.getElementById(id);
let currentUser = null, currentConversation = null, conversations = [], tickets = [], busy = false;
async function api(path, data) {
  const response = await fetch(path, {credentials:'same-origin', ...(data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});
  const value = await response.json();
  if (!response.ok) throw new Error(`${value.message || 'Ошибка запроса'} [${value.code || response.status}; ${value.request_id || ''}]`);
  return value;
}
function option(value, text) { const el = document.createElement('option'); el.value = value; el.textContent = text; return el; }
function lock(value) {
  busy = value;
  ['user','ticket','new','conversations','send'].forEach(id => $(id).disabled = value || (id !== 'user' && !currentUser));
  $('message').disabled = value;
}
async function action(fn) { if (busy) return; lock(true); $('status').textContent='Подождите…'; try { await fn(); $('status').textContent=''; } catch(e) { $('status').textContent=e.message; } finally { lock(false); } }
function renderMessage(role, text, payload={}) {
  const item = document.createElement('article'); item.className='message '+role;
  const title=document.createElement('div');title.className='role';title.textContent=role==='user'?'Вы':'TeamSpace';
  const body=document.createElement('div');body.className='text';body.textContent=text;item.append(title,body);
  if (payload.sources?.length) {
    const details=document.createElement('details'), summary=document.createElement('summary');summary.textContent='На основании чего дан ответ';details.append(summary);
    for(const source of payload.sources){const a=document.createElement('a');a.textContent=`${source.type==='ticket'?'Тикет':'Статья'}: ${source.title}${source.section?' · '+source.section:''}`;
      a.href=source.url;a.target='_blank';a.rel='noopener';details.append(a);}
    item.append(details);
  }
  $('messages').append(item);$('messages').scrollTop=$('messages').scrollHeight;
}
function ticketContext(){const ticket=tickets.find(t=>t.id===$('ticket').value);$('ticket-context').textContent=ticket?`${ticket.id} · ${ticket.subject} · ${ticket.status}${ticket.error_code?' · '+ticket.error_code:''}`:'Общий вопрос по документации, без выбранного обращения.';}
async function refreshConversations(){conversations=await api('/api/conversations');$('conversations').replaceChildren(option('','Выберите диалог'),...conversations.map(c=>option(c.id,`${c.ticket_id||'Без тикета'} · ${new Date(c.created_at).toLocaleString('ru-RU')}`)));$('conversations').value=currentConversation||'';}
async function restore(id){const messages=await api(`/api/conversations/${id}/messages`);currentConversation=id;localStorage.setItem('conversation:'+currentUser,id);$('messages').replaceChildren();messages.forEach(m=>renderMessage(m.role,m.content,m.payload));const c=conversations.find(c=>c.id===id);$('ticket').value=c?.ticket_id||'';ticketContext();$('active').textContent=`Диалог · ${c?.ticket_id||'без тикета'}`;$('conversations').value=id;}
async function loadProfile(){const profile=await api('/api/me');currentUser=profile.id;$('user').value=profile.id;$('profile').textContent=`${profile.name} · ${profile.plan} · ${profile.auth_method} · ${profile.product} ${profile.product_version}`;tickets=await api('/api/tickets');$('ticket').replaceChildren(option('','Без тикета'),...tickets.map(t=>option(t.id,`${t.id} — ${t.subject}`)));currentConversation=null;$('messages').replaceChildren();await refreshConversations();const saved=localStorage.getItem('conversation:'+currentUser);if(conversations.some(c=>c.id===saved))await restore(saved);ticketContext();}
async function newConversation(){const c=await api('/api/conversations',{ticket_id:$('ticket').value||null});currentConversation=c.id;await refreshConversations();await restore(c.id);}
$('user').addEventListener('change',()=>action(async()=>{const selected=$('user').value;if(!selected)return;currentUser=null;currentConversation=null;tickets=[];conversations=[];$('message').value='';$('ticket').replaceChildren(option('','Без тикета'));$('conversations').replaceChildren(option('','Выберите диалог'));$('messages').replaceChildren();$('profile').textContent='';$('ticket-context').textContent='';$('active').textContent='Новый пользователь';await api('/api/demo/session',{user_id:selected});localStorage.removeItem('conversation:'+selected);await loadProfile();}));
$('ticket').addEventListener('change',()=>{ticketContext();$('active').textContent='Создайте новый диалог для выбранного тикета';currentConversation=null;$('conversations').value='';$('messages').replaceChildren();});
$('new').addEventListener('click',()=>action(newConversation));
$('conversations').addEventListener('change',()=>action(async()=>{if($('conversations').value)await restore($('conversations').value);}));
$('chat-form').addEventListener('submit',event=>{event.preventDefault();const message=$('message').value.trim();if(!message||!currentUser)return;action(async()=>{if(!currentConversation)await newConversation();$('status').textContent='Ассистент изучает обращение и документацию…';const result=await api('/api/chat',{conversation_id:currentConversation,message});renderMessage('user',message);renderMessage('assistant',result.answer,result);$('message').value='';});});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();if(!busy)$('chat-form').requestSubmit();}});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{$('message').value=button.dataset.question;$('message').focus();}));
action(async()=>{const users=await api('/api/demo/users');$('user').append(...users.map(u=>option(u.id,u.name)));try{await loadProfile();}catch(e){if(!e.message.includes('SESSION_REQUIRED'))throw e;}});
