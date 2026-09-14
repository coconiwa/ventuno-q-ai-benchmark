const $ = id => document.getElementById(id);
let source = "camera";
let cameraAvailable = false;
let streamUrl = "";
let busy = false;
let activeModel = "2b";
let autoEnabled = false;
let autoPaused = false;
let autoTimer = null;
let autoErrors = 0;
let switchTimer = null;
const savedLanguage = localStorage.getItem("ventuno-language-v2");
let currentLanguage = savedLanguage === "ja" ? "ja" : "en";
const lane = kind => document.querySelector(`.lane.${kind}`);

const messages = {
  ja: {
    lead:"同じVLMと画像を用いた、CPUとHexagon NPU向け実用構成の比較",model:"使用モデル",runMode:"実行モード",manual:"手動",auto:"オート",interval:"撮影間隔",gettingReady:"開始準備中",pause:"一時停止",resume:"再開",cameraWaiting:"USBカメラ待機中",uploadHint:"画像を選んでも実行できます",run:"撮影して比較",measuring:"計測中…",chooseImage:"画像を選ぶ",backCamera:"カメラに戻す",privacy:"画像はRAM上だけで処理。保存・クラウド送信はしません。",independence:"独立したコミュニティプロジェクト。Qualcomm Technologies, Inc.との提携・承認関係なし",seconds:"秒",total:"合計時間 · TOTAL",inputProcess:"入力処理",generationSpeed:"生成速度",firstResponse:"初回応答",recentRuns:"直近の計測",runs:"回",method:"cache無効 · 実行順を交互に変更",switching:"モデルを切り替えています",restarting:"CPU / NPUモデルサーバーを再起動しています",pleaseWait:"読み込み完了まで、この画面のままお待ちください",elapsed:n=>`経過 ${n}秒`,switchComplete:"切り替え完了",switchFailed:"切り替えできませんでした",readyBoth:"CPU / NPU ともに準備できました",cameraMissing:"USBカメラが接続されていません",stoppedAfterRun:"現在の計測後に停止",stopped:"停止しました",paused:"一時停止中",nextCapture:n=>`次の撮影まで ${n}秒`,capturing:"撮影します",statusFailed:"ステータスを取得できません",modelChangeFailed:"モデルを変更できません",switched:name=>`${name} に切り替えました`,maxImage:"画像は10MB以下にしてください",imageLoadFailed:"画像を読み込めません",measuringBoth:"CPU / NPU 計測中",measureFailed:"計測を完了できませんでした",memoryRestart:"RAMを回復するためモデルサーバーを再起動します",memoryDone:"RAMを回復しました",errorStopped:"エラーが3回続いたため停止",autoStopped:"オート実行を停止しました",cpuPlaceholder:"実行すると、ここにCPUの応答が表示されます。",npuPlaceholder:"実行すると、ここにNPUの応答が表示されます。"
  },
  en: {
    lead:"Compare practical CPU and Hexagon NPU configurations using the same VLM and image.",model:"MODEL",runMode:"Run mode",manual:"Manual",auto:"Auto",interval:"Interval",gettingReady:"Getting ready",pause:"Pause",resume:"Resume",cameraWaiting:"Waiting for USB camera",uploadHint:"You can also run with an uploaded image",run:"Capture & compare",measuring:"Measuring…",chooseImage:"Choose image",backCamera:"Back to camera",privacy:"Images stay in RAM. Nothing is saved or sent to the cloud.",independence:"Independent community project. Not affiliated with or endorsed by Qualcomm Technologies, Inc.",seconds:"s",total:"TOTAL LATENCY",inputProcess:"INPUT",generationSpeed:"GENERATION",firstResponse:"FIRST TOKEN",recentRuns:"Recent runs",runs:"runs",method:"cache disabled · execution order alternates",switching:"Switching model",restarting:"Restarting the CPU and NPU model servers",pleaseWait:"Please keep this screen open while the model loads.",elapsed:n=>`${n}s elapsed`,switchComplete:"Switch complete",switchFailed:"Switch failed",readyBoth:"CPU and NPU are ready",cameraMissing:"USB camera is not connected",stoppedAfterRun:"Stops after the current run",stopped:"Stopped",paused:"Paused",nextCapture:n=>`Next capture in ${n}s`,capturing:"Capturing…",statusFailed:"Could not retrieve system status",modelChangeFailed:"Could not switch models",switched:name=>`Switched to ${name}`,maxImage:"Choose an image smaller than 10 MB",imageLoadFailed:"Could not load the image",measuringBoth:"Measuring CPU / NPU",measureFailed:"Could not complete the benchmark",memoryRestart:"Restarting model servers to recover RAM",memoryDone:"RAM recovery complete",errorStopped:"Stopped after three consecutive errors",autoStopped:"Automatic mode stopped",cpuPlaceholder:"The CPU response will appear here.",npuPlaceholder:"The NPU response will appear here."
  }
};
const t = (key,...args) => {const value=messages[currentLanguage][key];return typeof value==="function"?value(...args):value};
messages.ja.method="同一条件 · 実行順を交互に変更";
messages.en.method="same conditions · execution order alternates";
function applyLanguage(lang){
  currentLanguage=lang;localStorage.setItem("ventuno-language-v2",lang);document.documentElement.lang=lang;
  document.querySelectorAll("[data-i18n]").forEach(el=>{const value=t(el.dataset.i18n);if(value!==undefined)el.textContent=value});
  document.querySelectorAll("[data-i18n-aria]").forEach(el=>el.setAttribute("aria-label",t(el.dataset.i18nAria)));
  document.querySelectorAll(".language-switch button").forEach(button=>{const active=button.dataset.lang===lang;button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active))});
  document.querySelectorAll("#intervalSelect option").forEach(option=>option.textContent=lang==="ja"?`${option.value}秒`:`${option.value}s`);
  const placeholders=[messages.ja.cpuPlaceholder,messages.en.cpuPlaceholder,""], cpu=$("cpuAnswer"), npu=$("npuAnswer");
  if(placeholders.includes(cpu.textContent))cpu.textContent=t("cpuPlaceholder");
  if([messages.ja.npuPlaceholder,messages.en.npuPlaceholder,""].includes(npu.textContent))npu.textContent=t("npuPlaceholder");
  if(!busy)$("runButton").textContent=t("run");
}

function toast(message){const el=$("toast");el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),4200)}
function value(id,v,digits=2){$(id).textContent=(v===null||v===undefined||v===0)?"—":Number(v).toFixed(digits)}
function setState(kind,state){$(`${kind}State`).textContent=state.toUpperCase();lane(kind).classList.toggle("running",state==="running")}
function resetRace(){for(const k of ["cpu","npu"]){$(`${k}Answer`).textContent="";["Total","Prefill","Tps","Ttft"].forEach(m=>$(`${k}${m}`).textContent="—");$(`${k}Bar`).style.width="0";setState(k,"queued")}}
function applyResult(r){const k=r.backend;value(`${k}Total`,r.total_s,2);value(`${k}Prefill`,r.prefill_s,2);value(`${k}Tps`,r.decode_tps,1);value(`${k}Ttft`,r.ttft_s,2);$(`${k}Bar`).style.width="100%";setState(k,"done")}
function applyHistory(h){$("runs").textContent=h.runs;for(const k of ["cpu","npu"]){const m=h[k].total_s;$(k+"Rolling").textContent=m.p50==null?"— / —":`${m.p50.toFixed(2)} / ${m.p95.toFixed(2)} s`}}
function boardText(b){const ram=b.memory_used_gb==null?"RAM —":`RAM ${b.memory_used_gb}/${b.memory_total_gb} GB`;const temp=b.temperature_c==null?"TEMP —":`TEMP ${b.temperature_c}°C`;$("boardStats").textContent=`${ram} · ${temp}`}
function applyBackendInfo(data){for(const k of ["cpu","npu"]){if(data.backends&&data.backends[k])$(`${k}Proof`).textContent=data.backends[k].proof}}
function beginModelSwitch(label){
  const overlay=$("modelSwitchOverlay");let elapsed=0;
  $("switchTitle").textContent=t("switching");$("switchTarget").textContent=label;
  $("switchMessage").textContent=t("restarting");
  $("switchElapsed").textContent=t("elapsed",0);overlay.className="model-switch-overlay";overlay.hidden=false;
  document.body.classList.add("switching-model");clearInterval(switchTimer);
  switchTimer=setInterval(()=>{$("switchElapsed").textContent=t("elapsed",++elapsed)},1000);
}
function endModelSwitch(ok,label,message=""){
  clearInterval(switchTimer);switchTimer=null;
  const overlay=$("modelSwitchOverlay");overlay.classList.add(ok?"complete":"failed");
  $("switchTitle").textContent=ok?t("switchComplete"):t("switchFailed");
  $("switchTarget").textContent=label;
  $("switchMessage").textContent=ok?t("readyBoth"):message;
  $("switchElapsed").textContent=ok?"READY":"ERROR";
  return new Promise(resolve=>setTimeout(()=>{overlay.hidden=true;document.body.classList.remove("switching-model");resolve()},ok?1200:2600));
}

function stopAutoTimer(){
  if(autoTimer){clearInterval(autoTimer);autoTimer=null}
}
function setMode(mode){
  autoEnabled=mode==="auto";
  $("manualMode").classList.toggle("active",!autoEnabled);
  $("manualMode").setAttribute("aria-pressed",String(!autoEnabled));
  $("autoMode").classList.toggle("active",autoEnabled);
  $("autoMode").setAttribute("aria-pressed",String(autoEnabled));
  $("autoControls").hidden=!autoEnabled;
  $("runButton").hidden=autoEnabled;
  if(!autoEnabled){stopAutoTimer();autoPaused=false;$("pauseAuto").textContent=t("pause")}
}
function pauseAutomatic(message=t("paused")){
  stopAutoTimer();autoPaused=true;$("pauseAuto").textContent=t("resume");$("autoStatus").textContent=message;
}
function scheduleAutomatic(first=false){
  if(!autoEnabled||autoPaused||busy)return;
  stopAutoTimer();
  let remaining=first?3:Number($("intervalSelect").value);
  $("autoStatus").textContent=t("nextCapture",remaining);
  autoTimer=setInterval(()=>{
    remaining-=1;
    $("autoStatus").textContent=remaining>0?t("nextCapture",remaining):t("capturing");
    if(remaining<=0){stopAutoTimer();runBenchmark(true)}
  },1000);
}

$("manualMode").onclick=()=>{
  setMode("manual");
  $("autoStatus").textContent=busy?t("stoppedAfterRun"):t("stopped");
};
$("autoMode").onclick=()=>{
  if(!cameraAvailable){toast(t("cameraMissing"));return}
  if(source!=="camera")showCamera();
  autoPaused=false;autoErrors=0;setMode("auto");$("pauseAuto").textContent=t("pause");scheduleAutomatic(true);
};
$("pauseAuto").onclick=()=>{
  if(autoPaused){autoPaused=false;$("pauseAuto").textContent=t("pause");scheduleAutomatic(true)}
  else pauseAutomatic(busy?t("stoppedAfterRun"):t("paused"));
};
$("intervalSelect").onchange=()=>{if(autoEnabled&&!autoPaused&&!busy)scheduleAutomatic(false)};

async function refresh(){
  try{
    const data=await fetch(`/api/status?lang=${encodeURIComponent(currentLanguage)}`,{cache:"no-store"}).then(r=>r.json());
    cameraAvailable=data.board.camera; boardText(data.board); applyHistory(data.history);
    activeModel=data.model.active;$('modelSelect').value=activeModel;$('modelLabel').textContent=`${data.model.label} · ${data.model.options.find(x=>x.id===activeModel).detail}`;applyBackendInfo(data);
    if(!busy) for(const k of ["cpu","npu"]) setState(k,data.board.backends[k]?"ready":"offline");
    if(cameraAvailable && source==="camera") showCamera();
  }catch(e){toast(t("statusFailed"))}
}
$('modelSelect').onchange=async e=>{
  const previous=activeModel;const targetLabel=e.target.options[e.target.selectedIndex].text;if(autoEnabled)setMode("manual");busy=true;$('runButton').disabled=true;e.target.disabled=true;beginModelSwitch(targetLabel);
  try{
    const response=await fetch('/api/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:e.target.value})});
    const result=await response.json();if(!response.ok)throw new Error(currentLanguage==="en"?t("modelChangeFailed"):(result.error||t("modelChangeFailed")));
    activeModel=result.model.active;$('modelLabel').textContent=`${result.model.label} · ${result.model.options.find(x=>x.id===activeModel).detail}`;applyHistory({runs:0,cpu:{total_s:{p50:null,p95:null}},npu:{total_s:{p50:null,p95:null}}});await endModelSwitch(true,result.model.label);toast(t("switched",result.model.label));
  }catch(error){e.target.value=previous;await endModelSwitch(false,targetLabel,error.message);toast(error.message)}finally{busy=false;$('runButton').disabled=false;e.target.disabled=false;refresh()}
};
function showCamera(){source="camera";$("sourceLabel").textContent="LIVE CAMERA";$("empty").style.display="none";streamUrl=`/api/stream?t=${Date.now()}`;$("preview").src=streamUrl}

$("cameraButton").onclick=()=>{if(cameraAvailable)showCamera();else toast(t("cameraMissing"))};
$("fileInput").onchange=async e=>{
  const file=e.target.files[0];if(!file)return;
  if(file.size>10*1024*1024){toast(t("maxImage"));return}
  const data=await new Promise((ok,ng)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=ng;r.readAsDataURL(file)});
  const response=await fetch("/api/upload",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:file.name,data})});
  const result=await response.json();if(!response.ok){toast(currentLanguage==="en"?t("imageLoadFailed"):(result.error||t("imageLoadFailed")));return}
  source="upload";$("sourceLabel").textContent="UPLOADED · 640×480";$("empty").style.display="none";$("preview").src=data;
};

function runBenchmark(automatic=false){
  if(busy)return Promise.resolve(false);
  const button=$("runButton");busy=true;button.disabled=true;button.textContent=t("measuring");resetRace();$("scan").parentElement.classList.add("running");
  $("modelSelect").disabled=true;if(automatic)$("autoStatus").textContent=t("measuringBoth");
  const events=new EventSource(`/api/run?source=${encodeURIComponent(source)}&lang=${encodeURIComponent(currentLanguage)}`);
  let settled=false;
  return new Promise(resolve=>{
  events.addEventListener("snapshot",e=>{const d=JSON.parse(e.data);$("preview").src=`data:image/jpeg;base64,${d.jpeg}`;$("sourceLabel").textContent=d.source.toUpperCase()});
  events.addEventListener("order",e=>{const d=JSON.parse(e.data);$("orderLabel").textContent=`ORDER · ${d.order.map(x=>x.toUpperCase()).join(" → ")}`});
  events.addEventListener("phase",e=>{const d=JSON.parse(e.data);setState(d.backend,"running")});
  events.addEventListener("token",e=>{const d=JSON.parse(e.data);$(`${d.backend}Answer`).textContent+=d.text;$(`${d.backend}Answer`).scrollTop=9999});
  events.addEventListener("result",e=>applyResult(JSON.parse(e.data)));
  events.addEventListener("maintenance",e=>{
    const d=JSON.parse(e.data);
    if(automatic)$("autoStatus").textContent=d.state==="starting"?t("memoryRestart"):t("memoryDone");
    if(d.state==="starting")toast(t("memoryRestart"));
    if(d.state==="ready"){boardText(d.after);toast(t("memoryDone"))}
  });
  events.addEventListener("done",e=>{
    if(settled)return;settled=true;
    const d=JSON.parse(e.data);applyHistory(d.history);boardText(d.board);autoErrors=0;finish(true);events.close();resolve(true);
  });
  events.addEventListener("error",e=>{
    if(settled)return;settled=true;
    try{const d=JSON.parse(e.data);toast(currentLanguage==="en"?t("measureFailed"):d.message)}catch(_){toast(t("measureFailed"))}
    autoErrors+=1;finish(false);events.close();resolve(false);
  });
  function finish(ok){
    busy=false;button.disabled=false;button.textContent=t("run");$("modelSelect").disabled=false;$("scan").parentElement.classList.remove("running");
    if(automatic&&autoEnabled&&!autoPaused){
      if(!ok&&autoErrors>=3){pauseAutomatic(t("errorStopped"));toast(t("autoStopped"))}
      else scheduleAutomatic(false);
    }
  }
  });
}
$("runButton").onclick=()=>runBenchmark(false);

document.querySelectorAll(".language-switch button").forEach(button=>button.onclick=()=>{if(busy)return;applyLanguage(button.dataset.lang);refresh()});
applyLanguage(currentLanguage);setMode("manual");refresh();setInterval(refresh,10000);
