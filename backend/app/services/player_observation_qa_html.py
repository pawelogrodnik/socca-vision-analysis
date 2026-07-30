from __future__ import annotations

from html import escape
import json
from typing import Any


def render_player_observation_qa_html(manifest: dict[str, Any]) -> str:
    """Render the self-contained, persistent offline observation-QA editor."""

    embedded = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    title = escape(
        str((manifest.get("ui") or {}).get("title") or "Player observation QA")
    )
    filename = json.dumps(
        str(
            (manifest.get("ui") or {}).get("download_filename")
            or "player_observation_qa_reviewed.json"
        )
    )
    return (
        _HTML.replace("__TITLE__", title)
        .replace("__AUDIT__", embedded)
        .replace("__FILENAME__", filename)
    )


_HTML = """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0b1220;color:#eef2f8}
    body{margin:0}
    header{position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;gap:20px;padding:16px 24px;background:#0b1220ee;border-bottom:1px solid #273650}
    h1{font-size:22px;margin:0 0 5px}p{margin:0;color:#9aacbf}
    main{display:grid;grid-template-columns:minmax(0,1fr) 330px;align-items:start;gap:18px;padding:20px;max-width:1800px;margin:auto}
    #frame{position:relative;align-self:start;line-height:0;background:#05080f;border:1px solid #273650}
    #frame img{display:block;width:100%;height:auto}
    #overlay{position:absolute;top:0;left:0;display:block;width:100%;height:auto;cursor:pointer;touch-action:none}
    #overlay.drawing{cursor:crosshair}
    aside{display:grid;align-content:start;gap:12px;padding:16px;border:1px solid #273650;border-radius:8px;background:#101a2b}
    button,textarea{border:1px solid #3a4b67;border-radius:6px;background:#172338;color:#eef2f8;font:inherit}
    button{padding:10px 12px;cursor:pointer}button:disabled{cursor:not-allowed;opacity:.45}
    textarea{box-sizing:border-box;width:100%;min-height:88px;padding:10px;resize:vertical;line-height:1.4}
    textarea:focus{outline:2px solid #38bdf8;border-color:#38bdf8}
    button.primary{background:#16a34a;border-color:#16a34a;font-weight:700}
    button.danger{border-color:#ef4444}button.active{outline:3px solid #38bdf8}
    .actions{display:grid;gap:8px}.manual-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}
    .muted{color:#9aacbf}.selection{min-height:42px;color:#cbd5e1}
    .comment-label{display:grid;gap:6px;color:#cbd5e1;font-size:14px}
    .nav{display:flex;justify-content:space-between;gap:8px}
    .crop{position:relative;overflow:hidden;display:none;background:#05080f;border:1px solid #3a4b67}
    .crop img{position:absolute;max-width:none}
    .crop-target{position:absolute;border:3px solid #22d3ee;background:#22d3ee22;pointer-events:none}
    @media(max-width:900px){main{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>__TITLE__</h1>
      <p><span id="progress"></span> · zaznacz bbox lub narysuj brakującego zawodnika. Nic nie jest wysyłane do serwera.</p>
    </div>
    <button id="download" class="primary">Zakończ i pobierz JSON</button>
  </header>
  <main>
    <section id="frame"><img id="image" alt="Klatka QA"><canvas id="overlay"></canvas></section>
    <aside>
      <strong id="frameTitle"></strong>
      <p class="muted" id="frameInfo"></p>
      <div class="crop" id="crop"><img id="cropImage" alt="Powiększenie wybranego bboxa"><div class="crop-target" id="cropTarget"></div></div>
      <div class="selection" id="selection">Kliknij bbox.</div>
      <div class="actions">
        <button id="player">To zawodnik</button>
        <button id="false">Cień / fałszywa detekcja</button>
        <button id="missingA">Dodaj brakującego Team A</button>
        <button id="missingB">Dodaj brakującego Team B</button>
      </div>
      <div class="manual-actions">
        <button id="toggleTeam">Zmień team A/B</button>
        <button id="deleteManual" class="danger">Usuń dorysowany bbox</button>
        <button id="undo">Cofnij</button>
        <button id="reset">Reset audytu</button>
      </div>
      <p class="muted">Po wybraniu Teamu A/B przeciągnij ramkę bezpośrednio na brakującym zawodniku. Dorysowany bbox można kliknąć, zmienić lub usunąć.</p>
      <label class="comment-label" for="frameComment">Komentarz do tej klatki (opcjonalnie)
        <textarea id="frameComment" placeholder="Opisz dodatkowy błąd lub nietypową sytuację..."></textarea>
      </label>
      <div class="nav"><button id="previous">Poprzednia</button><button id="next">Następna</button></div>
    </aside>
  </main>
  <script>
    const audit=__AUDIT__;
    const storageKey=`player-observation-qa:${audit.source?.match_id||'unknown'}:${audit.source?.artifact_digests?.visible_observation_projection||'v1'}`;
    let index=0,selectedKey=null,selectedManualId=null,drawTeam=null,start=null,draftEnd=null,notice=null;
    const decisions={},missing=[],frameComments={},history=[];
    const image=document.getElementById('image');
    const overlay=document.getElementById('overlay');
    const context=overlay.getContext('2d');
    const frameComment=document.getElementById('frameComment');

    function current(){return audit.items[index]}
    function status(row){return decisions[row.detection_key]||row.initial_review_status||'pending'}
    function copy(value){return JSON.parse(JSON.stringify(value))}
    function replaceObject(target,source){for(const key of Object.keys(target))delete target[key];Object.assign(target,source||{})}
    function snapshot(){return {decisions:copy(decisions),missing:copy(missing),frameComments:copy(frameComments)}}
    function remember(){history.push(snapshot());if(history.length>100)history.shift()}
    function persist(){localStorage.setItem(storageKey,JSON.stringify({index,decisions,missing,frameComments}))}
    function restore(){
      try{
        const saved=JSON.parse(localStorage.getItem(storageKey)||'null');
        if(!saved)return;
        index=Math.max(0,Math.min(audit.items.length-1,Number(saved.index)||0));
        replaceObject(decisions,saved.decisions);
        missing.push(...(saved.missing||[]));
        replaceObject(frameComments,saved.frameComments);
      }catch(error){console.warn('Nie udało się przywrócić audytu',error)}
    }
    function restoreSnapshot(value){
      replaceObject(decisions,value.decisions);
      missing.splice(0,missing.length,...(value.missing||[]));
      replaceObject(frameComments,value.frameComments);
      selectedKey=null;selectedManualId=null;
      persist();updateFrameInfo();renderSelection();
    }
    function newManualId(){
      return `manual:${globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
    }
    function selected(){return current().detections.find(row=>row.detection_key===selectedKey)}
    function selectedManual(){return missing.find(row=>row.manual_annotation_id===selectedManualId)}
    function hit(row,x,y){const [x1,y1,x2,y2]=row.bbox_xyxy;return x>=x1&&x<=x2&&y>=y1&&y<=y2}
    function area(row){const [x1,y1,x2,y2]=row.bbox_xyxy;return (x2-x1)*(y2-y1)}
    function point(event){
      const rect=overlay.getBoundingClientRect();
      return [(event.clientX-rect.left)/rect.width*audit.video.width,(event.clientY-rect.top)/rect.height*audit.video.height];
    }
    function drawManualBox(row,label,dashed=false){
      const [x1,y1,x2,y2]=row.bbox_xyxy;
      const color=row.manual_annotation_id===selectedManualId?'#22d3ee':row.team_label==='A'?'#38bdf8':'#a78bfa';
      context.save();context.strokeStyle=color;context.fillStyle=color+'33';context.lineWidth=5;
      if(dashed)context.setLineDash([12,8]);
      context.strokeRect(x1,y1,x2-x1,y2-y1);context.fillRect(x1,y1,x2-x1,y2-y1);
      context.setLineDash([]);context.fillStyle=color;context.fillRect(x1,y1,54,22);
      context.fillStyle='#06101d';context.font='bold 14px system-ui';context.fillText(label,x1+5,y1+16);context.restore();
    }
    function drawOverlay(){
      if(!overlay.width)return;
      context.clearRect(0,0,overlay.width,overlay.height);
      for(const row of current().detections){
        const [x1,y1,x2,y2]=row.bbox_xyxy;
        const review=status(row);
        const color=row.detection_key===selectedKey?'#22d3ee':review==='false_detection'?'#ef4444':review==='player'?'#22c55e':'#facc15';
        context.strokeStyle=color;context.fillStyle=color+'33';context.lineWidth=4;
        context.strokeRect(x1,y1,x2-x1,y2-y1);context.fillRect(x1,y1,x2-x1,y2-y1);
        context.fillStyle=color;context.fillRect(x1,y1,20,18);context.fillStyle='#06101d';
        context.font='bold 12px system-ui';context.fillText(String(row.display_order),x1+5,y1+13);
      }
      for(const row of missing.filter(item=>item.frame_number===current().frame_number))drawManualBox(row,`+ ${row.team_label}`);
      if(start&&draftEnd&&drawTeam){
        drawManualBox({bbox_xyxy:[Math.min(start[0],draftEnd[0]),Math.min(start[1],draftEnd[1]),Math.max(start[0],draftEnd[0]),Math.max(start[1],draftEnd[1])],team_label:drawTeam},`+ ${drawTeam}`,true);
      }
    }
    function frameMissingCount(){return missing.filter(row=>row.frame_number===current().frame_number).length}
    function updateFrameInfo(){
      document.getElementById('frameInfo').textContent=`Wykrycia: ${current().detections.length} · dorysowane: ${frameMissingCount()}. Oznaczamy tylko błędne obserwacje; poprawne zostawiamy.`;
    }
    function showCrop(target){
      const crop=document.getElementById('crop'),cropImage=document.getElementById('cropImage'),cropTarget=document.getElementById('cropTarget');
      if(!target){crop.style.display='none';return}
      const [x1,y1,x2,y2]=target.bbox_xyxy,bw=x2-x1,bh=y2-y1,padx=bw*.8,pady=bh*.45;
      const cx=Math.max(0,x1-padx),cy=Math.max(0,y1-pady),cr=Math.min(audit.video.width,x2+padx),cb=Math.min(audit.video.height,y2+pady),cw=cr-cx,ch=cb-cy;
      crop.style.display='block';crop.style.aspectRatio=`${cw} / ${ch}`;cropImage.src=current().frame_filename;
      cropImage.style.width=`${audit.video.width/cw*100}%`;cropImage.style.left=`${-cx/cw*100}%`;cropImage.style.top=`${-cy/ch*100}%`;
      cropTarget.style.left=`${(x1-cx)/cw*100}%`;cropTarget.style.top=`${(y1-cy)/ch*100}%`;
      cropTarget.style.width=`${bw/cw*100}%`;cropTarget.style.height=`${bh/ch*100}%`;
    }
    function renderSelection(){
      const existing=selected(),manual=selectedManual(),target=existing||manual;
      document.getElementById('selection').textContent=existing?`BBox ${existing.display_order} · Team ${existing.team_label}`:manual?`Dorysowany bbox · Team ${manual.team_label}`:drawTeam?`Tryb rysowania Team ${drawTeam}: przeciągnij ramkę na zawodniku.`:notice||'Kliknij bbox.';
      document.getElementById('missingA').classList.toggle('active',drawTeam==='A');
      document.getElementById('missingB').classList.toggle('active',drawTeam==='B');
      document.getElementById('toggleTeam').disabled=!manual;
      document.getElementById('deleteManual').disabled=!manual;
      document.getElementById('undo').disabled=history.length===0;
      overlay.classList.toggle('drawing',Boolean(drawTeam));
      showCrop(target);drawOverlay();
    }
    function render(){
      const item=current();
      selectedKey=null;selectedManualId=null;drawTeam=null;start=null;draftEnd=null;notice=null;
      image.src=item.frame_filename;
      image.onload=()=>{overlay.width=image.naturalWidth||audit.video.width;overlay.height=image.naturalHeight||audit.video.height;drawOverlay()};
      document.getElementById('frameTitle').textContent=`Klatka ${index+1}/${audit.items.length} · ${item.time_sec} s`;
      frameComment.value=frameComments[item.frame_number]||'';
      updateFrameInfo();renderSelection();
      document.getElementById('progress').textContent=`Klatka ${index+1} z ${audit.items.length}`;
      persist();
    }

    document.getElementById('player').onclick=()=>{const row=selected();if(!row)return;remember();decisions[row.detection_key]='player';persist();renderSelection()};
    document.getElementById('false').onclick=()=>{const row=selected();if(!row)return;remember();decisions[row.detection_key]='false_detection';persist();renderSelection()};
    document.getElementById('missingA').onclick=()=>{selectedKey=null;selectedManualId=null;drawTeam='A';notice=null;renderSelection()};
    document.getElementById('missingB').onclick=()=>{selectedKey=null;selectedManualId=null;drawTeam='B';notice=null;renderSelection()};
    document.getElementById('previous').onclick=()=>{index=Math.max(0,index-1);render()};
    document.getElementById('next').onclick=()=>{index=Math.min(audit.items.length-1,index+1);render()};
    document.getElementById('toggleTeam').onclick=()=>{
      const row=selectedManual();if(!row)return;remember();
      row.team_label=row.team_label==='A'?'B':'A';row.updated_at=new Date().toISOString();
      notice=`Zmieniono na Team ${row.team_label}.`;persist();renderSelection();
    };
    document.getElementById('deleteManual').onclick=()=>{
      const row=selectedManual();if(!row)return;remember();
      missing.splice(missing.findIndex(item=>item.manual_annotation_id===row.manual_annotation_id),1);
      selectedManualId=null;notice='Usunięto dorysowany bbox.';persist();updateFrameInfo();renderSelection();
    };
    document.getElementById('undo').onclick=()=>{const previous=history.pop();if(previous)restoreSnapshot(previous)};
    document.getElementById('reset').onclick=()=>{
      if(!confirm('Usunąć wszystkie decyzje, dorysowane bboxy i komentarze tego audytu?'))return;
      remember();replaceObject(decisions,{});missing.splice(0);replaceObject(frameComments,{});
      localStorage.removeItem(storageKey);frameComment.value='';selectedKey=null;selectedManualId=null;
      notice='Audyt został zresetowany.';updateFrameInfo();renderSelection();
    };
    overlay.addEventListener('pointerdown',event=>{
      const [x,y]=point(event);
      if(drawTeam){
        start=[x,y];draftEnd=[x,y];notice=null;
        if(overlay.setPointerCapture&&event.pointerId!==undefined)overlay.setPointerCapture(event.pointerId);
        drawOverlay();return;
      }
      const manualHits=missing.filter(row=>row.frame_number===current().frame_number&&hit(row,x,y)).sort((left,right)=>area(left)-area(right));
      if(manualHits.length){selectedManualId=manualHits[0].manual_annotation_id;selectedKey=null}
      else{
        const hits=current().detections.filter(row=>hit(row,x,y)).sort((left,right)=>area(left)-area(right));
        selectedKey=hits[0]?.detection_key||null;selectedManualId=null;
      }
      renderSelection();
    });
    overlay.addEventListener('pointermove',event=>{if(!start||!drawTeam)return;draftEnd=point(event);drawOverlay()});
    overlay.addEventListener('pointerup',event=>{
      if(!start||!drawTeam)return;
      const team=drawTeam,end=point(event),left=Math.min(start[0],end[0]),top=Math.min(start[1],end[1]),right=Math.max(start[0],end[0]),bottom=Math.max(start[1],end[1]);
      start=null;draftEnd=null;
      if(right-left>8&&bottom-top>8){
        remember();
        const row={manual_annotation_id:newManualId(),frame_number:current().frame_number,team_label:team,bbox_xyxy:[left,top,right,bottom].map(value=>Number(value.toFixed(3))),reviewed_at:new Date().toISOString()};
        missing.push(row);selectedManualId=row.manual_annotation_id;drawTeam=null;notice=`Dodano bbox Team ${team}.`;persist();updateFrameInfo();
      }else notice='Ramka jest za mała. Przeciągnij ponownie.';
      renderSelection();
    });
    overlay.addEventListener('pointercancel',()=>{start=null;draftEnd=null;notice='Rysowanie anulowane.';renderSelection()});
    frameComment.addEventListener('input',()=>{
      const frameNumber=current().frame_number,value=frameComment.value;
      if(value.trim())frameComments[frameNumber]=value;else delete frameComments[frameNumber];
      persist();
    });
    document.getElementById('download').onclick=()=>{
      persist();
      const output=copy(audit);output.reviewed_at=new Date().toISOString();
      output.manual_review={detection_decisions:decisions,missing_players:missing,frame_comments:Object.entries(frameComments).map(([frameNumber,comment])=>({frame_number:Number(frameNumber),comment})).sort((left,right)=>left.frame_number-right.frame_number)};
      const blob=new Blob([JSON.stringify(output,null,2)+'\\n'],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download=__FILENAME__;link.click();URL.revokeObjectURL(url);
    };
    restore();render();
  </script>
</body>
</html>
"""
