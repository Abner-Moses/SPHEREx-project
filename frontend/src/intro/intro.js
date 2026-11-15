// Cinematic intro: Y/N sound gate -> 30 s pixel film (first visit) -> ULTRAKILL-style boot -> app.
// Ported from design/intro-mock.html; all audio is generated with Web Audio (no files).
// runIntro(root, { returning, onDone }) builds the DOM inside root and returns a dispose() for unmount.
import { PIGEON, PIGEON_COLORS } from '../eggs.js'

const HTML = `<section class="phase on" data-x="gate">
  <div class="hd"><span>SPHEREx // SKY EXPLORER</span><span>build 2026.11</span></div>
  <hr>
  <div>This transmission contains sound. Headphones recommended.</div>
  <div>Runtime: 37 s film · boot sequence · press Esc at any point to skip.</div>
  <div class="q">ENABLE AUDIO? <b>[Y/n]</b> <span class="blk"></span></div>
  <div class="yn"><button data-x="yes">Y · sound on</button><button data-x="no">N · silent</button></div>
</section>

<section class="phase" data-x="film">
  <div class="frame" data-x="frame">
    <canvas data-x="cv" width="384" height="160"></canvas>
    <div class="grain" data-x="grain"></div>
    <div class="vign"></div>
    <div class="ixc hook" data-x="cHook"></div>
    <div class="ixc big" data-x="c1">102 colours.</div>
    <div class="ixc big" data-x="c2">The whole sky.</div>
    <div class="ixc big" data-x="c3">Every six months.</div>
    <div class="ixc cap" data-x="c4" style="left:7%;bottom:12%">0.75 – 5.0 µm · infrared · all-sky survey</div>
    <div class="ixc year" data-x="cYear"><b data-x="yr">1916</b><span class="cap">Barnard's Star · motion exaggerated for the film</span></div>
    <div class="ixc cap" data-x="cCraft" style="left:7%;top:12%">SPHEREx · low Earth orbit · 2025–</div>
    <div class="ixc sub" data-x="cSub"><i>VOYAGER</i>Good, you're here. I'm Voyager. I'll be your guide.</div>
    <div class="ixc sub" data-x="cSub2"><i>VOYAGER</i>Fair warning: I know a lot about stars, and almost nothing about small talk.</div>
    <div class="ixc title" data-x="cTitle"><div class="w" data-x="tw">SPHEREx</div><div class="line" data-x="tl"></div><div class="s" data-x="ts">SKY EXPLORER</div></div>
    <div class="ixc press" data-x="cPress">PRESS ANY KEY ▸</div>
    <div class="ixc sign" data-x="cSign">A LOS PALOMOS TRANSMISSION</div>
  </div>
</section>

<section class="phase" data-x="boot">
  <div data-x="term"></div>
  <div class="scan"></div>
  <div class="crt" data-x="crt"></div>
</section>

<div class="bars top" data-x="barT"></div><div class="bars bot" data-x="barB"></div>
<div class="skip" data-x="skip">ESC · SKIP</div>`

// Demo cut (presentation): cut={from,to} plays only film seconds from..to (title, no pigeon), then a short boot at bootScale.
// audioCtx/audioOut: an external AudioContext and node to play into (the presentation records them); never closed here.
export function runIntro(root, { returning = false, onDone = () => {}, autoStart = false, timeScale = 1, cut = null, bootScale = timeScale, audioCtx = null, audioOut = null } = {}) {
root.innerHTML = HTML

const $=(id)=>root.querySelector('[data-x="'+id+'"]')
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches
const FW=384,FH=160,FPS=12
const BAYER=[0,8,2,10,12,4,14,6,3,11,1,9,15,7,13,5].map((v)=>(v+.5)/16)
const B4=(x,y)=>BAYER[(y&3)*4+(x&3)]
const clamp=(x,a,b)=>Math.min(b,Math.max(a,x))
const lerp=(a,b,t)=>a+(b-a)*t
const eio=(t)=>t*t*(3-2*t), eout=(t)=>1-(1-t)**3, ein=(t)=>t*t*t
const hash=(x,y)=>((((x|0)*73856093)^((y|0)*19349663))>>>0)

// ============================================================ phases
let phase='gate',muted=true,mode='first',timers=[]
function show(id){for(const s of root.querySelectorAll('.phase'))s.classList.toggle("on",s.dataset.x===id);phase=id;$('skip').classList.toggle('on',id==='film'||id==='boot')}
let ts=timeScale
function later(ms,fn){timers.push(setTimeout(fn,ms/ts))}
function clearTimers(){timers.forEach(clearTimeout);timers=[]}
function begin(sound){
  mode=returning?'return':'first'
  muted=!sound
  if(sound)audio.init()
  if(reduced&&!autoStart){toSite();return}
  if(mode==='return')startBoot(); else startFilm()
}
$('yes').onclick=()=>begin(true);$('no').onclick=()=>begin(false)
const onKey=(e)=>{
  const k=e.key.toLowerCase()
  if(phase==='gate'){if(k==='escape'){toSite();return}if(k==='y'||k==='enter')begin(true);else if(k==='n')begin(false);return}
  if(k==='escape'){toSite();return}
  if(phase==='film'&&filmT>=T_SIGN)startBoot();else if(phase==='film'&&filmT>=T_TITLE+2)filmStart=performance.now()-T_SIGN*1000
}
addEventListener('keydown',onKey)
$('skip').onclick=toSite
if(returning)$('gate').children[3].textContent='Boot sequence · press Esc at any point to skip.'
setTimeout(()=>$('yes').focus(),50)

// ============================================================ audio (Web Audio, generated)
const audio={
  ctx:null,
  init(){
    if(this.ctx){if(this.ctx.state==='suspended')this.ctx.resume();return}
    const A=this.ctx=audioCtx||new (window.AudioContext||window.webkitAudioContext)()
    // analog chain: soft tape saturation -> gentle high cut -> glue compression
    this.master=A.createGain();this.master.gain.value=.8
    const sat=A.createWaveShaper();sat.curve=this.tanhCurve(1.8);sat.oversample='2x'
    const tone=A.createBiquadFilter();tone.type='lowpass';tone.frequency.value=9500;tone.Q.value=.5
    const comp=A.createDynamicsCompressor();comp.threshold.value=-16;comp.ratio.value=3;comp.attack.value=.01;comp.release.value=.25
    this.master.connect(sat).connect(tone).connect(comp).connect(audioOut||A.destination)
    this.rev=A.createConvolver();this.rev.buffer=this.impulse(5.5)
    const wet=A.createGain();wet.gain.value=.5;this.rev.connect(wet).connect(this.master)
  },
  tanhCurve(k){const n=2048,c=new Float32Array(n);for(let i=0;i<n;i++){const x=i/(n-1)*2-1;c[i]=Math.tanh(k*x)/Math.tanh(k)}return c},
  impulse(sec){const A=this.ctx,n=A.sampleRate*sec,b=A.createBuffer(2,n,A.sampleRate);for(let c=0;c<2;c++){const d=b.getChannelData(c);let lp=0;for(let i=0;i<n;i++){lp=lp*.6+(Math.random()*2-1)*.4;d[i]=lp*Math.pow(1-i/n,2.4)}}return b},
  noiseBuf(sec,shape=(i,n)=>1){const A=this.ctx,n=Math.max(1,A.sampleRate*sec|0),b=A.createBuffer(1,n,A.sampleRate),d=b.getChannelData(0);for(let i=0;i<n;i++)d[i]=(Math.random()*2-1)*shape(i,n);return b},
  bus(){const g=this.ctx.createGain();g.connect(this.master);g.connect(this.rev);return g},
  newBus(){const g=this.bus();(this.buses??=[]).push(g);return g},
  stopAll(){if(!this.ctx)return;for(const b of this.buses||[]){b.gain.cancelScheduledValues(this.ctx.currentTime);b.gain.setTargetAtTime(0,this.ctx.currentTime,.05)}this.buses=[];for(const s of this.loops||[])try{s.stop()}catch{};this.loops=[]},
  dry(bus){const g=this.ctx.createGain();g.connect(bus);return g},

  // ---- kept as they were (the user likes these): organ pad and the plucked arpeggio
  organ(bus,f,t,dur,vol=.1,att=1.6){const A=this.ctx;const g=A.createGain();g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+att);g.gain.setValueAtTime(vol,t+Math.max(att,dur-2));g.gain.linearRampToValueAtTime(0,t+dur+2.2);g.connect(bus)
    // slow tape wow on the whole voice so it never sounds perfectly digital
    const lfo=A.createOscillator(),lg=A.createGain();lfo.frequency.value=.35+Math.random()*.2;lg.gain.value=5;lfo.connect(lg);lfo.start(t);lfo.stop(t+dur+2.4)
    for(const [m,a,det] of [[1,1,0],[1,.6,4],[2,.5,0],[3,.22,0],[4,.16,-3],[6,.06,0]]){const o=A.createOscillator();o.type='sine';o.frequency.value=f*m;o.detune.value=det;lg.connect(o.detune);const og=A.createGain();og.gain.value=a*.35;o.connect(og).connect(g);o.start(t);o.stop(t+dur+2.4)}},
  chord(bus,fs,t,dur,vol,att){fs.forEach((f)=>this.organ(bus,f,t,dur,vol/fs.length*2,att))},
  pluck(bus,f,t,vol=.07){const A=this.ctx,o=A.createOscillator(),g=A.createGain();o.type='triangle';o.frequency.value=f;g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+.008);g.gain.exponentialRampToValueAtTime(.0005,t+.42);o.connect(g).connect(bus);o.start(t);o.stop(t+.5)},

  // ---- new, physical-sounding sources
  // tape hiss bed under the whole film
  hiss(bus,t,dur,vol=.01){const A=this.ctx,s=A.createBufferSource();s.buffer=this.noiseBuf(2);s.loop=true;const hp=A.createBiquadFilter();hp.type='highpass';hp.frequency.value=4000;const g=A.createGain();g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+1);g.gain.setValueAtTime(vol,t+dur-1);g.gain.linearRampToValueAtTime(0,t+dur);s.connect(hp).connect(g).connect(this.master);s.start(t);s.stop(t+dur);(this.loops??=[]).push(s)},
  // a mechanical clock: resonant wooden/metal body, alternating tick and tock, slightly humanised
  clock(bus,t,vol=.16,tock=false){const A=this.ctx;t+=(Math.random()-.5)*.012;vol*=.85+Math.random()*.3
    const s=A.createBufferSource();s.buffer=this.noiseBuf(.03,(i,n)=>(1-i/n)**6)
    const g=A.createGain();g.gain.value=vol
    for(const [f,q,a] of tock?[[1350,9,1],[2900,12,.5]]:[[1900,9,1],[4100,12,.55]]){const bp=A.createBiquadFilter();bp.type='bandpass';bp.frequency.value=f*(.98+Math.random()*.04);bp.Q.value=q;const bg=A.createGain();bg.gain.value=a;s.connect(bp).connect(bg).connect(g)}
    g.connect(this.dry(bus));s.start(t)},
  // air movement between cuts: band-passed noise that sweeps up and pans across
  whoosh(bus,t,dur=.6,vol=.2,dir=1){const A=this.ctx,s=A.createBufferSource();s.buffer=this.noiseBuf(dur);const bp=A.createBiquadFilter();bp.type='bandpass';bp.Q.value=1.1;bp.frequency.setValueAtTime(250,t);bp.frequency.exponentialRampToValueAtTime(3200,t+dur*.85)
    const g=A.createGain();g.gain.setValueAtTime(.0001,t);g.gain.exponentialRampToValueAtTime(vol,t+dur*.8);g.gain.linearRampToValueAtTime(0,t+dur)
    const p=A.createStereoPanner();p.pan.setValueAtTime(-.7*dir,t);p.pan.linearRampToValueAtTime(.7*dir,t+dur);s.connect(bp).connect(g).connect(p).connect(bus);s.start(t)},
  // cinematic low boom: pitched body plus a soft beater transient
  boom(bus,t,vol=.9){const A=this.ctx,o=A.createOscillator(),g=A.createGain();o.type='sine';o.frequency.setValueAtTime(62,t);o.frequency.exponentialRampToValueAtTime(34,t+1.4);g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+.008);g.gain.exponentialRampToValueAtTime(.001,t+2.2);o.connect(g).connect(this.master);o.start(t);o.stop(t+2.3)
    const s=A.createBufferSource();s.buffer=this.noiseBuf(.06,(i,n)=>(1-i/n)**3);const lp=A.createBiquadFilter();lp.type='lowpass';lp.frequency.value=900;const ng=A.createGain();ng.gain.value=vol*.5;s.connect(lp).connect(ng).connect(bus);s.start(t)},
  // low brass "braam": stacked detuned saws through a closing low-pass and saturation
  braam(bus,t,vol=.22,dur=3.2){const A=this.ctx,lp=A.createBiquadFilter();lp.type='lowpass';lp.Q.value=2;lp.frequency.setValueAtTime(180,t);lp.frequency.linearRampToValueAtTime(1100,t+.12);lp.frequency.exponentialRampToValueAtTime(260,t+dur)
    const sh=A.createWaveShaper();sh.curve=this.tanhCurve(3);const g=A.createGain();g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+.06);g.gain.exponentialRampToValueAtTime(.001,t+dur);lp.connect(sh).connect(g).connect(bus)
    for(const [f,d] of [[55,-7],[55,7],[110,-4],[82.41,3],[110,9]]){const o=A.createOscillator();o.type='sawtooth';o.frequency.value=f;o.detune.value=d;o.connect(lp);o.start(t);o.stop(t+dur+.1)}},
  // a slow string-like ensemble: many detuned saws, filter opening over time
  ensemble(bus,fs,t,dur,vol=.12){const A=this.ctx,lp=A.createBiquadFilter();lp.type='lowpass';lp.Q.value=.7;lp.frequency.setValueAtTime(350,t);lp.frequency.exponentialRampToValueAtTime(2600,t+dur*.8)
    const g=A.createGain();g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+dur*.55);g.gain.setValueAtTime(vol,t+dur-1.5);g.gain.linearRampToValueAtTime(0,t+dur+1.5);lp.connect(g).connect(bus)
    for(const f of fs)for(const d of [-11,-4,4,12]){const o=A.createOscillator();o.type='sawtooth';o.frequency.value=f;o.detune.value=d+Math.random()*3;const og=A.createGain();og.gain.value=1/(fs.length*4);o.connect(og).connect(lp);o.start(t);o.stop(t+dur+1.6)}},
  // Voyager: a small servo motor whine, and a soft metal landing thud
  servo(bus,t,dur=.45,vol=.05){const A=this.ctx,o=A.createOscillator();o.type='sawtooth';o.frequency.setValueAtTime(120,t);o.frequency.linearRampToValueAtTime(260,t+dur*.6);o.frequency.linearRampToValueAtTime(190,t+dur)
    const bp=A.createBiquadFilter();bp.type='bandpass';bp.frequency.value=900;bp.Q.value=4;const g=A.createGain();g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+.05);g.gain.linearRampToValueAtTime(0,t+dur);o.connect(bp).connect(g).connect(this.dry(bus));o.start(t);o.stop(t+dur+.05)},
  thud(bus,t,vol=.35){const A=this.ctx,o=A.createOscillator(),g=A.createGain();o.frequency.setValueAtTime(140,t);o.frequency.exponentialRampToValueAtTime(55,t+.18);g.gain.setValueAtTime(vol,t);g.gain.exponentialRampToValueAtTime(.001,t+.3);o.connect(g).connect(this.dry(bus));o.start(t);o.stop(t+.32)
    this.click(bus,t+.004,2400,.12)},
  // short mechanical click (relays, shutters, drive heads)
  click(bus,t,f=2500,vol=.08){const A=this.ctx,s=A.createBufferSource();s.buffer=this.noiseBuf(.008,(i,n)=>(1-i/n)**2);const bp=A.createBiquadFilter();bp.type='bandpass';bp.frequency.value=f;bp.Q.value=5;const g=A.createGain();g.gain.value=vol;s.connect(bp).connect(g).connect(this.dry(bus));s.start(t)},
  // drive-head seek chatter: a burst of tiny irregular clicks
  chatter(bus,t,n=5,vol=.06){for(let i=0;i<n;i++)this.click(bus,t+i*(.008+Math.random()*.02),1800+Math.random()*2400,vol*(.6+Math.random()*.6))},
  // an old klaxon: two-tone sawtooth through a horn-shaped band-pass and grit
  klaxon(bus,t,f,dur=.09,vol=.05){const A=this.ctx,o=A.createOscillator();o.type='sawtooth';o.frequency.value=f;const bp=A.createBiquadFilter();bp.type='bandpass';bp.frequency.value=f*2.2;bp.Q.value=2.5;const sh=A.createWaveShaper();sh.curve=this.tanhCurve(4);const g=A.createGain();g.gain.setValueAtTime(vol,t);g.gain.setValueAtTime(vol,t+dur*.85);g.gain.linearRampToValueAtTime(0,t+dur);o.connect(bp).connect(sh).connect(g).connect(this.dry(bus));o.start(t);o.stop(t+dur+.02)},
  // CRT degauss "thunk" with its wobble, and a cooling fan spinning up
  degauss(bus,t,vol=.3){const A=this.ctx,o=A.createOscillator(),g=A.createGain(),l=A.createOscillator(),lg=A.createGain();o.frequency.value=60;l.frequency.value=8;lg.gain.value=14;l.connect(lg).connect(o.frequency);g.gain.setValueAtTime(vol,t);g.gain.exponentialRampToValueAtTime(.001,t+.7);o.connect(g).connect(this.dry(bus));o.start(t);o.stop(t+.75);l.start(t);l.stop(t+.75);this.click(bus,t,1200,.2)},
  fan(bus,t,dur=6,vol=.025){const A=this.ctx,s=A.createBufferSource();s.buffer=this.noiseBuf(2);s.loop=true;const lp=A.createBiquadFilter();lp.type='lowpass';lp.frequency.setValueAtTime(150,t);lp.frequency.exponentialRampToValueAtTime(900,t+2.5);const g=A.createGain();g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(vol,t+1.5);g.gain.setValueAtTime(vol,t+dur-.4);g.gain.linearRampToValueAtTime(0,t+dur);s.connect(lp).connect(g).connect(this.dry(bus));s.start(t);s.stop(t+dur);(this.loops??=[]).push(s)},
  // a pigeon taking off: a handful of soft feathered flaps, quickening then fading
  flutter(bus,t,n=7,vol=.07,pan=.5){const A=this.ctx;for(let i=0;i<n;i++){const ti=t+i*(.095-i*.004)+Math.random()*.01,s=A.createBufferSource();s.buffer=this.noiseBuf(.06,(j,m)=>Math.sin(Math.PI*j/m)**2);const bp=A.createBiquadFilter();bp.type='bandpass';bp.frequency.value=900+Math.random()*700;bp.Q.value=.9;const g=A.createGain();g.gain.value=vol*(1-i/(n+2))*(.8+Math.random()*.4);const pn=A.createStereoPanner();pn.pan.value=Math.max(-1,Math.min(1,pan+i*.06));s.connect(bp).connect(g).connect(pn).connect(this.dry(bus));s.start(ti)}},
  // the one honest beep: a PC-speaker POST beep at power-on
  post(bus,t){const A=this.ctx,o=A.createOscillator(),g=A.createGain();o.type='square';o.frequency.value=1000;g.gain.setValueAtTime(.035,t);g.gain.setValueAtTime(.035,t+.11);g.gain.linearRampToValueAtTime(0,t+.12);const lp=A.createBiquadFilter();lp.type='lowpass';lp.frequency.value=3000;o.connect(lp).connect(g).connect(this.dry(bus));o.start(t);o.stop(t+.13)},
}
const N={A1:55,F1:43.65,A2:110,C3:130.81,E3:164.81,F2:87.31,G2:98,F3:174.61,G3:196,A3:220,B3:246.94,C4:261.63,D4:293.66,E4:329.63,G4:392,A4:440,B4:493.88,C5:523.25,D5:587.33,E5:659.25,A5:880}
const CH={Am:[N.A2,N.E3,N.A3,N.C4],F:[N.F2,N.F3,N.A3,N.C4],C:[N.C3,N.G3,N.C4,N.E4],G:[N.G2,N.G3,N.B3,N.D4],Big:[N.A1,N.A2,N.E3,N.A3,N.B3,N.C4,N.E4]}
function filmAudio(){
  if(muted||!audio.ctx)return
  const A=audio.ctx,bus=audio.newBus(),t0=A.currentTime+.05,at=(s)=>t0+s
  audio.hiss(bus,at(0),37.5)
  // the clock: tick-tock that speeds up as the cutting speeds up
  {let i=0;for(let s=.5;s<12.5;s+=1)audio.clock(bus,at(s),.16,i++%2===1)}
  {let i=0;for(let s=12.5;s<17;s+=.5)audio.clock(bus,at(s),.14,i++%2===1)}
  // organ pad (unchanged)
  audio.organ(bus,N.A2,at(.4),6.5,.13,2.5)
  audio.chord(bus,CH.Am,at(3),4.4,.16,2.2)
  audio.chord(bus,CH.F,at(7),2.2,.16,.9)
  audio.chord(bus,CH.C,at(9),3.4,.16,.6)
  audio.chord(bus,CH.G,at(12.5),1,.15,.25)
  audio.chord(bus,CH.Am,at(13.5),1,.15,.2)
  audio.chord(bus,CH.F,at(14.5),1,.15,.2)
  audio.chord(bus,CH.G,at(15.5),1.4,.17,.3)
  // air between the fast cuts, alternating direction
  // the pigeon on the solar panel leaves
  audio.flutter(bus,at(5.6),8,.075,.35)
  audio.whoosh(bus,at(12.05),.5,.2,1);audio.whoosh(bus,at(13.05),.5,.2,-1);audio.whoosh(bus,at(14.05),.5,.2,1);audio.whoosh(bus,at(15.1),1.7,.24,-1)
  // the match cut lands on a boom
  audio.boom(bus,at(9),.85);audio.boom(bus,at(12.5),.4)
  // arpeggio (unchanged)
  const arp=[[7,9,N.A4,N.C5],[9,12.5,N.G4,N.E5],[12.5,13.5,N.B4,N.D5],[13.5,14.5,N.A4,N.E5],[14.5,15.5,N.A4,N.C5],[15.5,17,N.B4,N.D5]]
  for(const [a,b,f1,f2] of arp){let i=0;for(let s=a;s<b-.01;s+=.25)audio.pluck(bus,i++%2?f2:f1,at(s),.06)}
  // Voyager: the music drops to a low pad; a servo whine on the way in, a metal thud on landing, two shutter clicks for the blink
  audio.chord(bus,[N.A2,N.E3],at(17),9.6,.07,.5)
  // under the second line, the pad lifts a step: the same chord the boot's ALL SYSTEMS GO lands on later
  audio.chord(bus,[N.C3,N.G3],at(22.7),4.3,.05,.8)
  audio.servo(bus,at(17.05),.5);audio.thud(bus,at(17.6));audio.servo(bus,at(17.75),.25,.03)
  audio.click(bus,at(18.9),3200,.05);audio.click(bus,at(19.0),2600,.05)
  audio.click(bus,at(22.65),3200,.04);audio.click(bus,at(22.75),2600,.04)
  // hard cut to silence; then the title lands on a boom and a brass braam, and strings open slowly under the organ
  audio.boom(bus,at(T_TITLE),1)
  audio.braam(bus,at(T_TITLE),.24,3.4)
  audio.chord(bus,CH.Big,at(T_TITLE),7.2,.26,3)
  audio.ensemble(bus,[N.A3,N.C4,N.E4,N.A4],at(T_TITLE+.4),7)
  // the sign-off resolves on the opening organ note, so the film ends where it began
  audio.organ(bus,N.A2,at(T_SIGN+.2),2.6,.1,.6)
}

// ============================================================ film rendering (ImageData, 12 drawings per second)
const cv=$('cv'),g=cv.getContext('2d'),img=g.createImageData(FW,FH),D=new Uint32Array(img.data.buffer)
const rgb=(h)=>{const n=parseInt(h.slice(1),16);return (0xff000000|((n&255)<<16)|(n&0xff00)|(n>>16))>>>0}
const C={};for(const [k,v] of Object.entries({bk:'#000000',night:'#090d12',night2:'#0d131a',city:'#8a5a14',blue:'#4aa3ff',blueHi:'#bfe0ff',cream:'#e6dfcc',creamDim:'#9b9484',hot:'#fff6cf',amber:'#f2b705',amberDk:'#5a3a0b',gold:'#f2c25e',grey:'#3a3f44',grey2:'#525a61',steel:'#c7cdd2',steelDk:'#77818a',metal:'#3f474f',metalHi:'#a9b2ba',dark:'#050608',ring:'#101418',red:'#ff5a5f',redHi:'#ffd0c8',redDk:'#4a1d20',d1:'#7c8cff',d2:'#4fd1e0',d3:'#8fe07a',d4:'#f4d35e',d5:'#ff9b42',d6:'#ff5a5f',dust:'#3a3630',pnl:'#173352',pnl2:'#2f6aa8'}))C[k]=rgb(v)
const BAND=[C.d1,C.d2,C.d3,C.d4,C.d5,C.d6]
function P(x,y,c){x|=0;y|=0;if(x>=0&&y>=0&&x<FW&&y<FH)D[y*FW+x]=c}
function clear(c=C.bk){D.fill(c)}
const STARS=(()=>{let s=3;const r=()=>(s=(s*16807)%2147483647)/2147483647;return Array.from({length:300},()=>({x:r()*FW,y:r()*FH,b:r()}))})()
function stars(dim=1,colour=true){for(const s of STARS){if(s.b<1-dim)continue;P(s.x,s.y,colour&&s.b>.97?C.blueHi:s.b>.8?C.cream:C.grey)}}
function line(x0,y0,x1,y1,c,dot=1){const n=Math.max(Math.abs(x1-x0),Math.abs(y1-y0))|0;for(let i=0;i<=n;i+=dot)P(lerp(x0,x1,i/n),lerp(y0,y1,i/n),c)}

// --- the shots
function shotEarth(lt){ // 3–7 s: establishing shot
  clear();const z=1+lt*.016,cx=FW/2,cy=FH/2;stars(.9)
  const ex=FW*.38,ey=FH*2.05,R=FH*1.42,sunA=-1.2
  const sx=ex+Math.cos(sunA)*(R+1),sy=ey+Math.sin(sunA)*(R+1),rise=clamp(lt/2.4,0,1)
  for(let y=0;y<FH;y++)for(let x=0;x<FW;x++){
    const wx=cx+(x-cx)/z,wy=cy+(y-cy)/z,d=Math.hypot(wx-ex,wy-ey)-R,a=Math.atan2(wy-ey,wx-ex),lit=Math.max(0,Math.cos(a-sunA))
    if(d<0){let c=hash(wx,wy)%5===0?C.night2:C.night;if(hash(wx*3,wy*3)%211===0&&d<-3)c=C.city
      const k=lit**10*Math.exp(d/5)*rise;if(k>B4(x,y))c=k>.6?C.cream:C.blue;D[y*FW+x]=c}
    else if(d<5){const k=(1-d/5)*(.3+.7*lit**2)*(.5+.5*rise);if(k>B4(x,y))D[y*FW+x]=k>.75?C.blueHi:C.blue}
    const sd=Math.hypot(wx-sx,wy-sy),sk=Math.exp(-sd/(5+rise*9))*rise*1.2
    if(sk>B4(x,y)+.05)D[y*FW+x]=sk>.8?C.hot:sk>.45?C.gold:C.amberDk}
  const kx=Math.round(FW*.71+lt*1.6),ky=Math.round(FH*.33)
  craft(kx,ky)
  // a pigeon sitting on the solar panel; it bobs twice and leaves at 2.6 s
  if(lt<2.6)fpigeon(lt>1.2&&Math.floor(lt*3)%3===0?'bob':'perched',kx+4,ky+3)
  else{const k=lt-2.6;fpigeon(Math.floor(lt*10)%2?'up':'down',kx+4+k*70,ky+3-k*30-k*k*10)}
}
function craft(cx,cy){ // SPHEREx: three nested cone shields over a small bus and a flat solar panel
  for(let k=0;k<3;k++){const top=cy-10+k*5;for(let r=0;r<4;r++){const y=top+r,h=Math.round(10-k*1.6-r*.9);for(let x=cx-h;x<=cx+h;x++)P(x,y,r===0?C.cream:x<cx-1?C.steel:x<cx+2?C.metalHi:C.steelDk)}}
  for(let y=cy+5;y<=cy+8;y++)for(let x=cx-4;x<=cx+4;x++)P(x,y,x<cx-1?C.metalHi:C.metal)
  for(let x=cx-12;x<=cx+12;x++){P(x,cy+9,x%2?C.pnl:C.pnl2);P(x,cy+10,C.pnl)}
}
const PCOL={};for(const [k,v] of Object.entries(PIGEON_COLORS))PCOL[k]=rgb(v)
function fpigeon(frame,x,y,s=1){PIGEON[frame].forEach((row,r)=>[...row].forEach((ch,c)=>{const col=PCOL[ch];if(!col)return;for(let yy=0;yy<s;yy++)for(let xx=0;xx<s;xx++)P(Math.round(x)+c*s+xx,Math.round(y)+r*s+yy,col)}))}
function shotAperture(lt){ // 7–9 s: close-up, the filter sweeps across
  clear();const cx=FW/2,cy=FH/2,R=56+lt*4
  const sx=-70+(FW+140)*(lt/2)
  for(let y=0;y<FH;y++)for(let x=0;x<FW;x++){
    const r=Math.hypot(x-cx,y-cy)
    if(r>R+9)continue
    if(r>R){const k=.5+.5*Math.cos(Math.atan2(y-cy,x-cx)+2.3);D[y*FW+x]=r>R+7.5&&k>.6?C.metalHi:k>B4(x,y)?C.steelDk:C.metal;continue}
    let c=(r|0)%7===0?C.ring:C.dark
    const off=x-(sx-60);if(off>=0&&off<120){const k=.75*(1-Math.abs(off-60)/66);if(k>B4(x,y))c=BAND[Math.min(5,(off/20)|0)]}
    D[y*FW+x]=c}
}
function shotStar(lt){ // 9–12.5 s: match cut from the aperture to Barnard's Star, then 110 years of motion
  clear();stars(.45,false)
  const cx=FW/2,cy=FH/2
  if(lt<.34){const rr=lerp(60,2,ein(lt/.34));for(let y=0;y<FH;y++)for(let x=0;x<FW;x++){const r=Math.hypot(x-cx,y-cy);if(r<rr&&r>rr-2.5)D[y*FW+x]=C.red;else if(r<rr-2.5&&(r|0)%7===0)D[y*FW+x]=C.ring}return}
  const p=eio(clamp((lt-.34)/3,0,1)),x0=FW*.40,y0=FH*.62,x1=FW*.6,y1=FH*.4
  for(let i=0;i<=Math.floor(p*44);i++){const t=i/44;if(i%2===0)P(lerp(x0,x1,eio(t)),lerp(y0,y1,eio(t)),C.redDk)}
  const px=lerp(x0,x1,p),py=lerp(y0,y1,p)
  for(let y=-8;y<=8;y++)for(let x=-8;x<=8;x++){const r=Math.hypot(x,y),k=(1-r/8)**2*.9;if(k>B4(px+x|0,py+y|0))P(px+x,py+y,C.red)}
  for(let y=-1;y<=1;y++)for(let x=-1;x<=1;x++)P(px+x,py+y,C.red);P(px,py,C.redHi)
}
function shotPullback(lt){ // 12.5–17 s: four fast cuts, each wider than the last
  clear()
  if(lt<1){stars(.6,false);const s=1.7-.6*lt,cx=FW*.45,cy=FH*.5
    const pts=[[0,-30],[-11,-13],[12,-15],[-15,6],[16,4],[-6,22],[11,23],[-30,1],[31,-2]],ed=[[0,1],[0,2],[1,3],[2,4],[3,5],[4,6],[3,7],[4,8],[1,2]]
    for(const [a,b] of ed)line(cx+pts[a][0]*s,cy+pts[a][1]*s,cx+pts[b][0]*s,cy+pts[b][1]*s,C.creamDim,2)
    for(const [x,y] of pts){P(cx+x*s,cy+y*s,C.cream);P(cx+x*s+1,cy+y*s,C.cream)}
    const bx=cx+22*s,by=cy+9*s;for(let y=-1;y<=1;y++)for(let x=-1;x<=1;x++)P(bx+x,by+y,C.red);return}
  if(lt<2){stars(.5,false);const R=62,cx=FW/2,cy=FH/2,rot=(lt-1)*.9
    const dot=(la,lo)=>{const x=Math.cos(la)*Math.cos(lo+rot),y=Math.sin(la),z=Math.cos(la)*Math.sin(lo+rot);P(cx+x*R,cy-y*R,z>0?C.steel:C.grey)}
    for(let la=-60;la<=60;la+=30)for(let a=0;a<6.283;a+=.035)dot(la*Math.PI/180,a)
    for(let lo=0;lo<360;lo+=30)for(let la=-90;la<=90;la+=2.4)dot(la*Math.PI/180,lo*Math.PI/180);return}
  if(lt<3){const cx=FW/2,cy=FH/2;for(let y=0;y<FH;y++)for(let x=0;x<FW;x++){const d=(y-cy)+(x-cx)*.25,n=(hash(x>>1,y>>1)%100)/100;let k=Math.exp(-((d/15)**2))*(.55+.45*n);if(Math.abs(d+1)<2.2&&n>.35)k*=.15;if(k>B4(x,y)*.9+.04)D[y*FW+x]=k>.78?C.hot:k>.5?C.cream:k>.3?C.creamDim:C.dust}stars(.25);return}
  const cx=FW/2,cy=FH/2,a=150,b=64,f=clamp((lt-3)/1.25,0,1),front=cx-a+2*a*f
  for(let y=0;y<FH;y++)for(let x=0;x<FW;x++){const q=((x-cx)/a)**2+((y-cy)/b)**2;if(q>1.04)continue;if(q>.97){D[y*FW+x]=C.grey2;continue}
    if(x<front-1){if(.68>B4(x,y))D[y*FW+x]=BAND[((x>>2)+6)%6]}else if(x<front+1)D[y*FW+x]=C.hot;else D[y*FW+x]=C.ring}
}
// --- Voyager (Direction B, slab probe), rendered once per expression to small canvases
const VCOL={K:'#0b0d10',W:'#dfe3e6',w:'#8f989f',i:'#b5bcc2',A:'#7f888f',R:'#e0393b',G:'#73490f',H:'#c08226',Y:'#f2c25e',V:'#090b0f',E:'#f2b705',e:'#fff6cf',l:'#4a300b',d:'#7a5c08',S:'#3f474f',s:'#77818a',t:'#a9b2ba',z:'#22282e',c:'#173352',p:'#2f6aa8',B:'#4aa3ff'}
const EXPR={deadpan:(dx,dy)=>(Math.abs(dx)<=2&&(dy===-1||dy===0))?(dx===-2&&dy===-1?'e':'E'):null,surprised:(dx,dy)=>(Math.abs(dx)<=1&&dy>=-2&&dy<=1)?(dx===-1&&dy===-2?'e':'E'):null,blink:(dx,dy)=>(Math.abs(dx)<=2&&dy===0)?'d':null,unimpressed:(dx,dy)=>(Math.abs(dx)<=2&&dy===0)?'E':(Math.abs(dx)<=2&&dy===-1)?'l':null}
const rr=(x,y,cx,cy,hx,hy,r)=>{const qx=Math.abs(x-cx)-hx+r,qy=Math.abs(y-cy)-hy+r;return Math.hypot(Math.max(qx,0),Math.max(qy,0))+Math.min(Math.max(qx,qy),0)-r}
const vh=(x,y)=>(((x*73856093)^(y*19349663))>>>0)%9
function vlit(x,y,cx,cy,[a,b,c],cr){const k=(x-cx)*.55+(y-cy)*.8;let i=k<-3?2:k>3.5?0:1;if(cr){const h=vh(x,y);if(h===0)i=Math.min(2,i+1);else if(h===1)i=Math.max(0,i-1)}return [a,b,c][i]}
function voyPx(x,y,ex){
  if(Math.hypot(x-26,y-2)<=1.1)return 'R'
  if(((x-22)/5.5)**2+((y-4)/2)**2<=1&&y<=4)return ((x-22)/3.8)**2+((y-3.6)/1)**2<=1?'i':(x<22?'W':'w')
  if(x===20&&y>=5&&y<=6)return 'A'
  if((x>=2&&x<=6||x>=26&&x<=30)&&y>=14&&y<=22)return((x%2===0)||(y%3===0))?'p':'c'
  if((x===7||x===8||x===24||x===25)&&y===18)return 'A'
  if(rr(x,y,16,17.5,7,10.5,2)<=0){
    if(y>=15&&y<=21){if(x>=11&&x<=21&&y>=16&&y<=20){const m=EXPR[ex](x-16,y-18);return m||'V'}return vlit(x,y,16,18,['G','H','Y'],true)}
    if(y===11||y===25)return 'z'
    if((x===11||x===21)&&(y===9||y===27))return 'z'
    return vlit(x,y,16,17.5,['S','s','t'],false)}
  return null}
const VSPR={}
for(const ex of Object.keys(EXPR)){const gg=[];for(let y=0;y<32;y++){gg[y]=[];for(let x=0;x<32;x++)gg[y][x]=voyPx(x,y,ex)}const o=gg.map((r)=>r.slice());for(let y=0;y<32;y++)for(let x=0;x<32;x++){const c=gg[y][x];if(c&&'GHYSst'.includes(c)&&!gg[y][x+1])o[y][x]='B';if(!c&&(gg[y][x-1]||gg[y][x+1]||(gg[y-1]&&gg[y-1][x])||(gg[y+1]&&gg[y+1][x])))o[y][x]='K'}
  const c=document.createElement('canvas');c.width=c.height=32;const cg=c.getContext('2d');o.forEach((r,y)=>r.forEach((v,x)=>{if(v){cg.fillStyle=VCOL[v];cg.fillRect(x,y,1,1)}}));VSPR[ex]=c}
function shotVoyager(lt){ // 17–24 s: the cameo, in front of the dimmed all-sky map (drawn after putImageData)
  shotPullback(4.25);for(let y=0;y<FH;y++)for(let x=0;x<FW;x++)if(B4(x,y)>.28)D[y*FW+x]=C.bk
  let x=FW*.5,y=FH*.4,sx=1,sy=1,ex='deadpan'
  if(lt<.5){const k=eout(lt/.5);x=lerp(-40,FW*.5,k);y=lerp(FH*.05,FH*.4,k);sx=1.18;sy=.86}
  else if(lt<.66){const k=(lt-.5)/.16;sx=1.24-.24*k;sy=.78+.22*k;ex='surprised'}
  else if(lt<.84){y-=Math.round(3*Math.sin((lt-.66)/.18*Math.PI));ex='surprised'}
  else{y+=Math.round(Math.sin(lt*2.4)*1);if((lt>1.9&&lt<2.0)||(lt>5.6&&lt<5.7))ex='blink';else if(lt>6.8&&lt<8.6)ex='unimpressed'}
  return {x,y,sx,sy,ex}
}
function shotSign(lt,ruleY){ // 31–34 s: the pigeon from the Earth shot comes back and lands on the title rule; crew credit
  const s=2,x=FW/2-9,y0=Math.round(ruleY)-6*s // feet (sprite row 5, two pixels tall) end just on top of the rule
  if(lt<.55){const k=eout(lt/.55);fpigeon(Math.floor(lt*12)%2?'up':'down',lerp(FW+20,x,k),lerp(-24,y0,k)-Math.sin(Math.PI*k)*10,s)}
  else fpigeon(Math.floor((lt-.55)*2.5)%2?'bob':'perched',x,y0,s)
}
function shotTitle(lt){ // 21–30 s: a warm light leak on the right
  clear();stars(.35,false)
  const k0=Math.min(1,lt/2.2)*.38
  for(let y=0;y<FH;y++)for(let x=FW-260;x<FW;x++){const k=Math.exp(-(((x-FW)**2)+((y-FH*.3)**2))/(2*62*62))*k0;if(k>B4(x,y)+.02)D[y*FW+x]=k>.25?C.amber:C.amberDk}
}

// --- edit decision list
// 0 hook · 3 Earth + craft · 7 aperture · 9 Barnard's Star · 12.5 pull-back to the all-sky map · 17 Voyager arrives in
// front of that map (two lines, 10 s: 4.5 s + 4.3 s on screen) · 27.5 title · 34 the pigeon lands on the title rule · 37 boot
const T_VOY=17,T_TITLE=27.5,T_SIGN=34,T_END=37
const FLASH=[[7,C.cream],[9,C.red],[12.5,C.cream],[13.5,C.cream],[14.5,C.cream]] // at most 3 in any second; one frame each
const CARDS=[['cHook',.6,2.9],['cCraft',3.6,6.8],['c1',12.55,13.45],['c2',13.55,14.45],['c3',14.55,15.45],['c4',15.6,17],['cYear',9.35,12.5],['cSub',18.0,22.5],['cSub2',22.8,27.1],['cTitle',T_TITLE+.3,99],['cPress',T_TITLE+4,T_SIGN],['cSign',T_SIGN+.9,99]]
let filmStart=0,filmT=0,lastDraw=-1,raf=0,signed=false,signRule=null
// where the title's amber rule sits, in film pixels (it is HTML, so it moves with the screen size)
function ruleY(){if(signRule==null){const fr=$('frame').getBoundingClientRect(),r=$('tl').getBoundingClientRect();signRule=fr.height?((r.top-fr.top)/fr.height)*FH:FH*.47}return signRule}
function startFilm(){
  clearTimers();show('film');filmStart=performance.now()-(cut?cut.from*1000/timeScale:0);lastDraw=-1;if(cut)cutAudio();else filmAudio()
  $('cHook').textContent='';raf=requestAnimationFrame(filmFrame)
}
function filmFrame(now){
  if(phase!=='film')return
  filmT=(now-filmStart)/1000*timeScale
  const T=Math.floor(filmT*FPS)/FPS
  if(T!==lastDraw){lastDraw=T;drawFilm(T)}
  if(filmT>=(cut?cut.to:T_END)){startBoot();return}
  raf=requestAnimationFrame(filmFrame)
}
function drawFilm(T){
  let voy=null
  if(T<3)clear()
  else if(T<7)shotEarth(T-3)
  else if(T<9)shotAperture(T-7)
  else if(T<12.5)shotStar(T-9)
  else if(T<17)shotPullback(T-12.5)
  else if(T<T_TITLE-.5)voy=shotVoyager(T-T_VOY)
  else if(T<T_TITLE)clear()
  else if(T<T_SIGN||cut)shotTitle(T-T_TITLE)
  else{shotTitle(T-T_TITLE);shotSign(T-T_SIGN,ruleY());if(!signed){signed=true;if(!muted&&audio.ctx)audio.flutter(audio.buses?.[0]??audio.bus(),audio.ctx.currentTime,5,.06,0)}}
  for(const [t,c] of FLASH)if(T>=t&&T<t+1/FPS){for(let y=0;y<FH;y++)for(let x=0;x<FW;x++)if(.6>B4(x,y))D[y*FW+x]=c}
  g.putImageData(img,0,0)
  if(voy){g.save();g.imageSmoothingEnabled=false;g.translate(Math.round(voy.x),Math.round(voy.y+30));g.scale(voy.sx*2,voy.sy*2);g.drawImage(VSPR[voy.ex],-16,-32);g.restore()}
  // cards: hard cuts, no fades
  for(const [id,a,b] of CARDS)$(id).style.opacity=(T>=a&&T<b&&!(cut&&(id==='cSign'||id==='cPress')))?1:0
  // the hook types itself out
  const hook='Light takes time.';$('cHook').textContent=hook.slice(0,clamp(Math.floor((T-.6)*12),0,hook.length))+(T<2.9&&Math.floor(T*2)%2?'_':'')
  if(T>=9.35)$('yr').textContent=String(1916+Math.round(110*eio(clamp((T-9.34)/3,0,1))))
  if(T>=T_TITLE+.3){const k=clamp((T-T_TITLE-.3)/1.6,0,1);$('tw').style.letterSpacing=lerp(1.4,.42,eout(k)).toFixed(3)+'em';$('tl').style.width=(eout(clamp((T-T_TITLE-1.4)/.8,0,1))*60)+'%';$('ts').style.opacity=T>=T_TITLE+2.2?1:0}
  // sign-off: the title's words leave, the rule stays for the pigeon to stand on
  const sk=cut?0:clamp((T-T_SIGN-.5)/.6,0,1);$('tw').style.opacity=1-sk;$('ts').style.opacity=T>=T_TITLE+2.2?1-sk:0
  $('cPress').style.visibility=!cut&&T>=T_TITLE+4&&T<T_SIGN&&Math.floor(T)%2===0?'visible':'hidden'
  // gate weave and moving grain
  $('frame').style.transform=`translate(-50%,-50%) translate(${(hash(T*12,1)%3)-1}px,${(hash(T*12,2)%3)-1}px)`
  $('grain').style.backgroundPosition=`${hash(T*12,3)%160}px ${hash(T*12,4)%160}px`
}

// ============================================================ boot (fast, dense, ~7 s)
const term=$('term')
function out(html,cls){const d=document.createElement('div');if(cls)d.className=cls;d.innerHTML=html;term.appendChild(d);while(term.children.length>60)term.firstChild.remove();term.scrollTop=1e9;return d}
// line sounds are relay clicks and drive chatter; the warning is a klaxon
function bootBeep(f,dur=.025,vol=.04,type='square'){if(muted||!audio.ctx)return;const t=audio.ctx.currentTime;if(type==='sawtooth')audio.klaxon(audio.bootBus,t,f,dur,vol);else if(Math.random()<.35)audio.chatter(audio.bootBus,t,3+(Math.random()*4|0),vol*1.4);else audio.click(audio.bootBus,t,f*1.6,vol*1.8)}
const SLOW=1.35
function glitch(ms){term.classList.add('glitch');later(ms,()=>term.classList.remove('glitch'));if(!muted&&audio.ctx){audio.chatter(audio.bootBus,audio.ctx.currentTime,6,.08);audio.whoosh(audio.bootBus,audio.ctx.currentTime,ms/1000+.05,.1)}}
const LOGO_FONT={S:['████','█   ','████','   █','████'],P:['████','█  █','████','█   ','█   '],H:['█  █','█  █','████','█  █','█  █'],E:['████','█   ','███ ','█   ','████'],R:['███ ','█  █','███ ','█ █ ','█  █'],X:['█   █',' █ █ ','  █  ',' █ █ ','█   █']}
const LOGO=[0,1,2,3,4].map((r)=>[...'SPHEREX'].map((ch)=>LOGO_FONT[ch][r].replace(/./g,(c)=>c+c)).join('  '))
function bar(n,of=20){return '['+'#'.repeat(n)+'.'.repeat(of-n)+']'}
function startBoot(){
  if(cut){shortBoot();return}
  cancelAnimationFrame(raf);clearTimers();audio.stopAll();show('boot');term.innerHTML=''
  if(!muted&&audio.ctx){audio.bootBus=audio.newBus()}
  // letterbox bars snap open (they hold the film's shape for a beat first)
  const bh=Math.max(0,(innerHeight-innerWidth/2.4)/2);for(const b of [$('barT'),$('barB')]){b.style.transition='none';b.style.height=bh+'px'}
  later(60,()=>{for(const b of [$('barT'),$('barB')]){b.style.transition='height 250ms cubic-bezier(.2,.8,.2,1)';b.style.height='0'}})
  // CRT power-on
  const crt=$('crt');crt.style.transition='none';crt.style.opacity=1;crt.style.transform='translateY(-50%) scaleX(0)';crt.style.height='2px'
  later(30,()=>{crt.style.transition='transform 140ms ease-out';crt.style.transform='translateY(-50%) scaleX(1)';if(!muted&&audio.ctx){const n=audio.ctx.currentTime;audio.degauss(audio.bootBus,n);audio.fan(audio.bootBus,n,11);audio.post(audio.bootBus,n+.35)}})
  later(190,()=>{crt.style.transition='height 110ms ease-in, opacity 160ms 110ms';crt.style.height='100%';crt.style.opacity=0})
  let t=320
  const at=(ms,fn)=>later(t+=ms*SLOW,fn)
  at(0,()=>{out('SPHEREx SKY-EXPLORER FIRMWARE v0.3.1          (c) 2026  OBSERVER BUILD','c');bootBeep(1400)})
  at(60,()=>{out('MACHINE ......... OBSERVER-01   CORE ..... WEBGL2   DETECTORS ..... 6','d');bootBeep(1600)})
  at(60,()=>{out(' ')})
  // memory test: a counter that runs fast
  at(40,()=>{const el=out('MEMORY TEST ..... 000000K');let k=0;const iv=setInterval(()=>{k=Math.min(524288,k+Math.round(9000+Math.random()*24000));el.textContent='MEMORY TEST ..... '+String(k).padStart(6,'0')+'K'+(k>=524288?'  OK':'');if(k%3===0)bootBeep(2200+Math.random()*600,.012,.02);if(k>=524288){clearInterval(iv);bootBeep(1800,.05)}},28*SLOW);timers.push(iv)})
  t+=820*SLOW
  const dev=['photon shield · outer','photon shield · middle','photon shield · inner','telescope · 20 cm','focal plane · short-wave','focal plane · long-wave','star tracker','reaction wheels ×4']
  dev.forEach((d,i)=>at(45,()=>{out(`BUS 0.${i}  ${d.padEnd(28,'.')} <span class="b">OK</span>`);bootBeep(1200+i*90)}))
  at(45,()=>{out(`BUS 0.8  ${'homing module · PGN-1'.padEnd(28,'.')} <span class="b">CALIBRATED</span>`);bootBeep(1950)})
  at(80,()=>out(' '))
  ;['D1','D2','D3','D4','D5','D6'].forEach((d,i)=>at(i?150:40,()=>{const range=['0.75–1.09','1.10–1.62','1.63–2.41','2.42–3.82','3.83–4.41','4.42–5.00'][i];const el=out('');let n=0;const iv=setInterval(()=>{n=Math.min(20,n+4);el.innerHTML=`CALIBRATE ${d} ${range} µm  <span style="color:var(--d${i+1},#fff)">${bar(n)}</span> ${n>=20?'<span class="b">LOCKED</span>':''}`;if(n>=20)clearInterval(iv)},28*SLOW);timers.push(iv);bootBeep(900+i*140,.04)}))
  // a warning that flickers, then resolves
  at(260,()=>{const el=out('!! WARNING  SHIELD TEMP 61.2 K  > LIMIT 60.0 K','r');glitch(90);let n=0;const iv=setInterval(()=>{el.style.visibility=n++%2?'visible':'hidden';bootBeep(n%2?440:330,.08,.05,'sawtooth');if(n>=6){clearInterval(iv);el.style.visibility='visible'}},90);timers.push(iv)})
  at(620,()=>{out('   → passive cooling engaged .............. 54.8 K  <span class="b">RESOLVED</span>');bootBeep(1500,.06)})
  at(60,()=>out(' '))
  // checksum dump
  for(let i=0;i<9;i++)at(32,()=>{out('<span class="d">'+Array.from({length:12},()=>(Math.random()*65536|0).toString(16).padStart(4,'0').toUpperCase()).join(' ')+'</span>');if(i%2===0)bootBeep(2600,.01,.02)})
  at(40,()=>{const el=out('');let n=0;const iv=setInterval(()=>{n=Math.min(20,n+2);el.innerHTML=`LOADING STAR CATALOGUE  ${bar(n)}  ${Math.round(1214*n/20).toLocaleString()} stars`;if(n>=20){clearInterval(iv);bootBeep(1900,.05)}},22*SLOW);timers.push(iv)})
  at(320,()=>{out('VOYAGER PERSONALITY ..... HUMOR=50  DETAIL=60  TONE=DEADPAN','a');bootBeep(700,.08,.05,'triangle')})
  at(260,()=>{out('<span class="a">VOYAGER:</span> Online. Give me a second, the dish is still warming up.');bootBeep(990,.05,.04,'sine')})
  at(900,()=>{out(' ');glitch(120)})
  // the logo burst, row by row
  const logoBox=document.createElement('div');logoBox.className='logo'
  at(120,()=>{term.appendChild(logoBox);if(!muted&&audio.ctx){const n=audio.ctx.currentTime;audio.boom(audio.bootBus,n,.6);audio.braam(audio.bootBus,n,.16,1.6)}})
  LOGO.forEach((row,i)=>at(55,()=>{logoBox.textContent+=(i?'\n':'')+row;if(i===2)glitch(60)}))
  at(260,()=>out(' '))
  at(40,()=>{const el=out('<span class="inv">ALL SYSTEMS GO</span>');let n=0;const iv=setInterval(()=>{el.style.visibility=n++%2?'visible':'hidden';if(n>=4){clearInterval(iv);el.style.visibility='visible'}},110);timers.push(iv)
    if(!muted&&audio.ctx){const n0=audio.ctx.currentTime;audio.chord(audio.bootBus,[110,164.81,220,277.18],n0,1.1,.2,.04)}})
  // CRT power-down into the site
  at(820,()=>{if(mode==='first')outro();term.style.transition='transform 160ms ease-in';term.style.transform='scaleY(.004)';if(!muted&&audio.ctx){const n=audio.ctx.currentTime;audio.click(audio.bootBus,n,900,.25);audio.thud(audio.bootBus,n+.02,.2)}})
  at(170,()=>{term.style.transition='transform 120ms ease-in';term.style.transform='scaleY(.004) scaleX(0)'})
  at(180,()=>{term.style.transition='none';term.style.transform='';toSite()})
}

// ============================================================ site placeholder
let done=false,tail=700
// first visit only: the film's main chord and a last arpeggio ring on into the site, then fade
// ---- demo cut: the title's own score, then a short boot (essential lines only) at bootScale
function cutAudio(){
  if(muted||!audio.ctx)return
  const A=audio.ctx,bus=audio.newBus(),t0=A.currentTime+.05,at=(x)=>t0+Math.max(0,x-cut.from)/timeScale
  audio.hiss(bus,at(cut.from),(cut.to-cut.from)/timeScale+.5,.008)
  audio.boom(bus,at(T_TITLE),1);audio.braam(bus,at(T_TITLE),.24,3.4)
  audio.chord(bus,CH.Big,at(T_TITLE),(cut.to-T_TITLE)/timeScale,.26,3)
  audio.ensemble(bus,[N.A3,N.C4,N.E4,N.A4],at(T_TITLE+.4),(cut.to-T_TITLE-.4)/timeScale)
}
function shortBoot(){
  cancelAnimationFrame(raf);clearTimers();audio.stopAll();show('boot');term.innerHTML='';ts=bootScale
  if(!muted&&audio.ctx)audio.bootBus=audio.newBus()
  const crt=$('crt');crt.style.transition='none';crt.style.opacity=1;crt.style.transform='translateY(-50%) scaleX(0)';crt.style.height='2px'
  later(30,()=>{crt.style.transition='transform 140ms ease-out';crt.style.transform='translateY(-50%) scaleX(1)';if(!muted&&audio.ctx){const n=audio.ctx.currentTime;audio.degauss(audio.bootBus,n);audio.post(audio.bootBus,n+.25)}})
  later(190,()=>{crt.style.transition='height 110ms ease-in, opacity 160ms 110ms';crt.style.height='100%';crt.style.opacity=0})
  let t=320
  const at=(ms,fn)=>later(t+=ms,fn)
  at(0,()=>{out('SPHEREx SKY-EXPLORER FIRMWARE v0.3.1          (c) 2026  OBSERVER BUILD','c');bootBeep(1400)})
  ;['telescope · 20 cm','focal plane · 0.75–5.0 µm','homing module · PGN-1'].forEach((d,i)=>at(i?90:180,()=>{out(`BUS 0.${i}  ${d.padEnd(28,'.')} <span class="b">${i===2?'CALIBRATED':'OK'}</span>`);bootBeep(1200+i*150)}))
  ;['D1','D2','D3','D4','D5','D6'].forEach((d,i)=>at(i?70:160,()=>{out(`CALIBRATE ${d}  <span style="color:var(--d${i+1},#fff)">${bar(20)}</span> <span class="b">LOCKED</span>`);bootBeep(900+i*140,.03)}))
  at(220,()=>{out('<span class="a">VOYAGER:</span> Online.');bootBeep(990,.05,.04,'sine')})
  const logoBox=document.createElement('div');logoBox.className='logo'
  at(260,()=>{term.appendChild(logoBox);glitch(80);if(!muted&&audio.ctx){const n=audio.ctx.currentTime;audio.boom(audio.bootBus,n,.6);audio.braam(audio.bootBus,n,.16,1.4)}})
  LOGO.forEach((row,i)=>at(50,()=>{logoBox.textContent+=(i?'\n':'')+row}))
  at(220,()=>{const el=out('<span class="inv">ALL SYSTEMS GO</span>');let n=0;const iv=setInterval(()=>{el.style.visibility=n++%2?'visible':'hidden';if(n>=4){clearInterval(iv);el.style.visibility='visible'}},90);timers.push(iv)
    if(!muted&&audio.ctx){const n0=audio.ctx.currentTime;audio.chord(audio.bootBus,[110,164.81,220,277.18],n0,1,.2,.04)}})
  at(620,()=>{term.style.transition='transform 160ms ease-in';term.style.transform='scaleY(.004)';if(!muted&&audio.ctx){const n=audio.ctx.currentTime;audio.click(audio.bootBus,n,900,.25);audio.thud(audio.bootBus,n+.02,.2)}})
  at(170,()=>{term.style.transition='transform 120ms ease-in';term.style.transform='scaleY(.004) scaleX(0)'})
  at(140,()=>{term.style.transition='none';term.style.transform='';toSite()})
}
function outro(){if(muted||!audio.ctx)return;const A=audio.ctx,b=audio.bus(),n=A.currentTime+.05;audio.chord(b,CH.Am,n,2.8,.15,.35);[N.A4,N.C5,N.E5,N.A5,N.E5].forEach((f,i)=>audio.pluck(b,f,n+.35+i*.25,i===4?.03:.05));tail=7000}
function dispose(){if(done)return;done=true;cancelAnimationFrame(raf);clearTimers();audio.stopAll();removeEventListener('keydown',onKey);const ctx=audio.ctx;if(!ctx||audioCtx)return
  if(tail>700){const t=ctx.currentTime+tail/1000;audio.master.gain.setValueAtTime(audio.master.gain.value,t-1.6);audio.master.gain.linearRampToValueAtTime(0,t-.1)}
  setTimeout(()=>{try{ctx.close()}catch{}},tail)}
function toSite(){if(done)return;dispose();onDone()}

if(autoStart)begin(!!audioCtx) // with an external audio graph the intro plays its own sound into it
return dispose
}
