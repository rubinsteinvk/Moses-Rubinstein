const scenes=[
{title:'Сцена I', lines:[
 {de:'O Moses, hörst du den Ruf?',ru:'О Моисей, слышишь ты зов?',note:'Рабочая демонстрационная строка. Полный текст и word-level тайминг будут внесены после проверки авторитетного либретто и записи.'},
 {de:'Der Herr spricht zu seinem Volke.',ru:'Господь говорит к Своему народу.',note:'Порядок слов сохраняется там, где это позволяет русский синтаксис.'}
]},
{title:'Сцена II',lines:[{de:'—',ru:'—',note:'Текст сцены подготовляется.'}]},
{title:'Сцена III',lines:[{de:'—',ru:'—',note:'Текст сцены подготовляется.'}]},
{title:'Сцена IV',lines:[{de:'—',ru:'—',note:'Текст сцены подготовляется.'}]},
{title:'Сцена V',lines:[{de:'—',ru:'—',note:'Текст сцены подготовляется.'}]},
{title:'Сцена VI',lines:[{de:'—',ru:'—',note:'Текст сцены подготовляется.'}]},
{title:'Сцена VII',lines:[{de:'—',ru:'—',note:'Текст сцены подготовляется.'}]},
{title:'Сцена VIII',lines:[{de:'—',ru:'—',note:'Текст сцены подготовляется.'}]}
];
const list=document.getElementById('sceneList'), text=document.getElementById('text'), mobile=document.getElementById('mobileScene');
function words(s){return s.split(/(\s+)/).map((x,i)=>/\s+/.test(x)?x:`<span class="word" data-i="${i}">${x}</span>`).join('')}
function render(n){const s=scenes[n]; document.querySelectorAll('.scene').forEach((e,i)=>e.classList.toggle('active',i===n)); mobile.value=n; text.innerHTML=`<h2 class="scene-heading">${s.title}</h2><div class="sync-hint">При воспроизведении звучащее немецкое слово подсвечивается одновременно в левой и правой колонках; русский эквивалент — только справа.</div>`+s.lines.map((l,i)=>`<div class="line"><div class="de">${words(l.de)}</div><div class="ru">${words(l.ru)}</div></div>${l.note?`<div class="note">${l.note}</div>`:''}`).join('');}
scenes.forEach((s,i)=>{const b=document.createElement('button');b.className='scene';b.textContent=s.title;b.onclick=()=>render(i);list.appendChild(b);const o=document.createElement('option');o.value=i;o.textContent=s.title;mobile.appendChild(o)});
mobile.onchange=()=>render(+mobile.value);render(0);
const body=document.body;document.getElementById('theme').onclick=()=>body.classList.toggle('dark');
let snowOn=true;document.getElementById('snow').onclick=()=>{snowOn=!snowOn;document.getElementById('snowLayer').style.display=snowOn?'block':'none'};
const notes=document.getElementById('showNotes');const lang=document.getElementById('showLang');const meaning=document.getElementById('showMeaning');notes.onchange=()=>document.body.classList.toggle('notes-off',!notes.checked);lang.onchange=meaning.onchange=()=>{};
// The final build will replace these placeholders with verified word-level timings.
window.MOSES_SYNC=[];
for(let i=0;i<26;i++){const f=document.createElement('span');f.className='flake';f.textContent='❄';f.style.left=Math.random()*100+'%';f.style.fontSize=(8+Math.random()*10)+'px';f.style.animationDuration=(9+Math.random()*14)+'s';f.style.animationDelay=(-Math.random()*20)+'s';document.getElementById('snowLayer').appendChild(f)}
