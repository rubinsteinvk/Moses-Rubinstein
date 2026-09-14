const list=document.getElementById('sceneList'),text=document.getElementById('text'),mobile=document.getElementById('mobileScene');
const scenes=[
 {title:'Сцена I',lines:[{de:'—',ru:'—',note:'Сцена будет заполнена после подготовки текста.'}]},
 {title:'Сцена II',lines:[{de:'—',ru:'—',note:'Сцена будет заполнена после подготовки текста.'}]},
 {title:'Сцена III',lines:[{de:'—',ru:'—',note:'Сцена будет заполнена после подготовки текста.'}]},
 {title:'Сцена IV',lines:[{de:'—',ru:'—',note:'Сцена будет заполнена после подготовки текста.'}]},
 {title:'Сцена V — O ew’ger Vater',lines:[]},
 {title:'Сцена VI',lines:[{de:'—',ru:'—',note:'Сцена будет заполнена после подготовки текста.'}]},
 {title:'Сцена VII',lines:[{de:'—',ru:'—',note:'Сцена будет заполнена после подготовки текста.'}]},
 {title:'Сцена VIII',lines:[{de:'—',ru:'—',note:'Сцена будет заполнена после подготовки текста.'}]}
];
let timing=[];
const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\\"':'&quot;'}[c]||c));
function render(n){const s=scenes[n];document.querySelectorAll('.scene').forEach((e,i)=>e.classList.toggle('active',i===n));mobile.value=n;text.innerHTML=`<h2 class="scene-heading">${esc(s.title)}</h2><div class="sync-hint">Немецкое слово будет подсвечиваться одновременно слева и справа; русский эквивалент — только справа. Временные метки появятся после word-level alignment.</div>`+s.lines.map(l=>`<div class="line"><div class="de">${l.words?l.words.map((w,j)=>`<span class="word de-word" data-word="${j}">${esc(w.de)}</span>`).join(' '):esc(l.de)}</div><div class="ru">${l.words?l.words.map((w,j)=>`<span class="word ru-word" data-word="${j}">${esc(w.ru)}</span>`).join(' '):esc(l.ru)}</div></div>${l.note?`<div class="note">${esc(l.note)}</div>`:''}`).join('')}
scenes.forEach((s,i)=>{const b=document.createElement('button');b.className='scene';b.textContent=s.title;b.onclick=()=>render(i);list.appendChild(b);const o=document.createElement('option');o.value=i;o.textContent=s.title;mobile.appendChild(o)});mobile.onchange=()=>render(+mobile.value);
fetch('data/picture5.json').then(r=>r.json()).then(d=>{scenes[4].lines=d.lines.map(x=>({words:x.de.map((de,i)=>({de,ru:x.ru[i]||''}))}));timing=d.timing||[];if(+mobile.value===4)render(4)}).catch(()=>{});
function syncWords(t){document.querySelectorAll('.active-word').forEach(e=>e.classList.remove('active-word'));if(!timing.length)return;const x=timing.find(v=>t>=v.start&&t<v.end);if(x)document.querySelectorAll(`[data-word="${x.index}"]`).forEach(e=>e.classList.add('active-word'));}window.syncMosesWords=syncWords;
const body=document.body;document.getElementById('theme').onclick=()=>body.classList.toggle('dark');let snowOn=true;document.getElementById('snow').onclick=()=>{snowOn=!snowOn;document.getElementById('snowLayer').style.display=snowOn?'block':'none'};document.getElementById('showNotes').onchange=e=>body.classList.toggle('notes-off',!e.target.checked);document.getElementById('showLang').onchange=()=>{};document.getElementById('showMeaning').onchange=()=>{};
for(let i=0;i<26;i++){const f=document.createElement('span');f.className='flake';f.textContent='❄';f.style.left=Math.random()*100+'%';f.style.fontSize=(8+Math.random()*10)+'px';f.style.animationDuration=(9+Math.random()*14)+'s';f.style.animationDelay=(-Math.random()*20)+'s';document.getElementById('snowLayer').appendChild(f)}render(4);
