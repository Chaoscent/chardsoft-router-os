window.Page=window.Page||{};

window.Page.modem={

timer:null,

// Mobile data settings as saved (the PIN and password stay on the router).
mobile:null,

async update(){

    const res=await fetch("/api/modem");

    if(res.status===401){
        location.href="/login";
        return;
    }

    const d=await res.json();

    // Unknown values are null: show "--", never invented numbers.
    const show=(value,unit="")=>value===null||value===undefined?"--":`${value}${unit}`;

    modemNotice.classList.toggle("hidden",d.state==="ready");

    modemNotice.textContent=
        d.state==="absent"
            ?"No modem found. Plug in the modem; this page updates by itself."
            :d.state==="no_modemmanager"
            ?`${d.model||"The modem"} was found, but ModemManager is not installed (sudo apt install modemmanager), so it cannot be used.`
            :`${d.model||"The modem"} was found but does not answer yet. It can take a minute after startup.`;

    carrier.textContent=show(d.carrier);
    network.textContent=d.state==="absent"?"No modem":show(d.network);
    signal.textContent=show(d.signal,"%");

    rsrp.textContent=show(d.rsrp," dBm");
    rsrq.textContent=show(d.rsrq," dB");
    sinr.textContent=show(d.sinr," dB");

    band.textContent=show(d.band);
    sim.textContent=d.lock==="sim-pin"?"Waiting for PIN":d.lock==="sim-puk"?"Blocked (PUK)":show(d.sim);
    imei.textContent=show(d.imei);

    this.renderSim(d);
    this.renderDataLink(d);

    const onlyV6=d.connection==="activated"&&d.ipv6&&!d.ipv4;

    mobileConnection.textContent=onlyV6?"Connected (IPv6 only)":{
        activated:"Connected",
        activating:"Connecting...",
        deactivating:"Disconnecting...",
        deactivated:"Not connected"
    }[d.connection]||(this.mobile?.enabled?"Not connected":"Off");

},

renderDataLink(d){

    usbMode.textContent=d.usb_mode?d.usb_mode.toUpperCase():"--";

    mobileAddresses.textContent=[d.ipv4,d.ipv6].filter(Boolean).join(" · ")||"--";

    const sw=d.usb_switch||{};

    // Restarted by the watchdog in the last 15 minutes.
    const restarted=d.auto_restart&&Date.now()/1000-d.auto_restart<900
        ?`The data link was stuck, so the modem was restarted automatically at ${new Date(d.auto_restart*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}.`
        :"";

    const show=d.data_dropped||sw.running||(sw.message&&sw.success===false)||restarted;

    modemDataCard.classList.toggle("hidden",!show);

    if(!show)return;

    modemDataInfo.textContent=d.quectel&&d.usb_mode==="mbim"
        ?`Mobile data is connected, but the modem's received data is discarded: ${d.rx_errors} received packets were dropped as errors. Quectel modems do this in MBIM mode on Linux. QMI mode fixes it; the modem restarts once (about a minute without mobile data).`
        :d.data_dropped
            ?`Mobile data is connected, but ${d.rx_errors} received packets were dropped as errors. The router restarts the modem by itself if this goes on.`
            :restarted;

    modemSwitchQmi.classList.toggle("hidden",!(d.quectel&&d.usb_mode==="mbim"));
    modemSwitchQmi.disabled=!!sw.running;

    modemDataStatus.textContent=sw.running
        ?"Switching... the modem restarts, this page updates by itself."
        :sw.message||"";

},

async switchQmi(){

    modemSwitchQmi.disabled=true;
    modemDataStatus.textContent="Starting...";

    const res=await fetch("/api/modem/usb-mode",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({mode:"qmi"})
    });

    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    modemDataStatus.textContent=data.message;

    await this.update();

},

renderSim(d){

    const locked=d.lock==="sim-pin"||d.lock==="sim-puk";

    simPinCard.classList.toggle("hidden",!locked);

    if(!locked)return;

    const puk=d.lock==="sim-puk";

    simPinInfo.textContent=puk
        ?"The SIM is blocked after too many wrong PINs. Unblock it with its PUK in a phone, then put it back."
        :`The SIM is locked. Enter its PIN to use mobile data${d.pin_retries?` (${d.pin_retries} attempts left)`:""}. It is sent once per click, never retried by itself.`;

    simPinInput.disabled=simPinUnlock.disabled=puk;

},

async unlock(){

    const pin=simPinInput.value.trim();

    if(!/^\d{4,8}$/.test(pin)){
        simPinStatus.textContent="The PIN has 4 to 8 digits.";
        return;
    }

    simPinUnlock.disabled=true;
    simPinStatus.textContent="Unlocking...";

    const res=await fetch("/api/modem/unlock",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({pin,remember:simPinRemember.checked})
    });

    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    simPinUnlock.disabled=false;
    simPinInput.value="";
    simPinStatus.textContent=data.success?`✓ ${data.message}`:data.message;

    await this.loadMobile();
    await this.update();

},

async loadMobile(){

    const res=await fetch("/api/modem/settings");

    if(!res.ok)return;

    this.mobile=await res.json();

    mobileEnabledInput.value=String(this.mobile.enabled);
    mobileRoamingInput.value=String(this.mobile.roaming);
    mobileApnInput.value=this.mobile.apn||"";
    mobileUsernameInput.value=this.mobile.username||"";
    mobilePasswordInput.value="";
    mobilePasswordInput.placeholder=this.mobile.password_saved?"Saved (leave empty to keep)":"Usually not needed";

    mobilePinNote.textContent=this.mobile.pin_saved
        ?"The SIM PIN is saved: the SIM is unlocked at every start."
        :"";

    mobileForgetPin.classList.toggle("hidden",!this.mobile.pin_saved);

    ["mobileEnabledInput","mobileRoamingInput"].forEach(id=>ChaosSelect.refresh(document.getElementById(id)));

},

async saveMobile(extra={}){

    mobileSave.disabled=true;
    mobileStatus.textContent="Applying...";

    const res=await fetch("/api/modem/settings",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            enabled:mobileEnabledInput.value==="true",
            roaming:mobileRoamingInput.value==="true",
            apn:mobileApnInput.value.trim(),
            username:mobileUsernameInput.value,
            password:mobilePasswordInput.value,
            ...extra
        })
    });

    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    mobileSave.disabled=false;
    mobileStatus.textContent=data.success?`✓ ${data.message}`:data.message||"Failed to save.";

    await this.loadMobile();
    await this.update();

},

// ------------------------------------------------------------
// Data limit
// ------------------------------------------------------------

formatGb(bytes){

    return `${(bytes/1e9).toFixed(2)} GB`;

},

renderUsage(data){

    this.usage=data;

    dataUsedNow.textContent=data.limit
        ?`${this.formatGb(data.used)} of ${this.formatGb(data.limit)} (${data.percent}%)`
        :this.formatGb(data.used);

},

async loadUsage(){

    const res=await fetch("/api/data-usage");

    if(!res.ok)return;

    const data=await res.json();
    const s=data.settings;

    dataLimitInput.value=s.limit_gb?String(s.limit_gb):"";
    dataDayInput.value=String(s.billing_day);
    dataWarnInput.value=String(s.warn_percent);
    dataBlockInput.value=String(s.block_at_limit);

    // A value saved earlier that is not in the list stays selectable.
    if(dataWarnInput.value!==String(s.warn_percent)){
        dataWarnInput.add(new Option(`${s.warn_percent}%`,String(s.warn_percent)));
        dataWarnInput.value=String(s.warn_percent);
    }

    ["dataDayInput","dataWarnInput","dataBlockInput"].forEach(id=>ChaosSelect.refresh(document.getElementById(id)));

    this.renderUsage(data);

},

async saveUsage(){

    const limit=dataLimitInput.value.trim();

    if(limit&&!(Number(limit)>=0)){
        dataLimitStatus.textContent="Enter the data per period in GB, or leave it empty for no limit.";
        return;
    }

    dataLimitSave.disabled=true;
    dataLimitStatus.textContent="Saving...";

    const res=await fetch("/api/data-usage/settings",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            limit_gb:limit?Number(limit):0,
            billing_day:Number(dataDayInput.value),
            warn_percent:Number(dataWarnInput.value),
            block_at_limit:dataBlockInput.value==="true"
        })
    });

    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    dataLimitSave.disabled=false;
    dataLimitStatus.textContent=data.success?"✓ Saved.":data.message||"Failed to save.";

    if(data.status)this.renderUsage(data.status);

},

async correctUsage(reset){

    const value=dataUsedInput.value.trim();

    if(!reset&&!(value!==""&&Number(value)>=0)){
        dataUsedStatus.textContent="Enter the used data in GB, as your carrier's app shows it.";
        return;
    }

    if(reset&&!await ChaosModal.confirm({
        title:"Set the count to zero?",
        subtitle:"The data used so far in this period is no longer counted.",
        confirmText:"Set to zero"
    }))return;

    dataUsedSave.disabled=dataUsedReset.disabled=true;

    const res=await fetch("/api/data-usage/used",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(reset?{reset:true}:{used_gb:Number(value)})
    });

    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    dataUsedSave.disabled=dataUsedReset.disabled=false;
    dataUsedStatus.textContent=data.success?`✓ ${data.message}`:data.message;

    if(data.success)dataUsedInput.value="";
    if(data.status)this.renderUsage(data.status);

},

// ------------------------------------------------------------
// GNSS
// ------------------------------------------------------------

GNSS_LEVELS:{
    fix:["online","Position"],
    searching:["warning","Searching"],
    starting:["warning","Starting"],
    off:["muted","Off"],
    unsupported:["muted","Not available"],
    no_modem:["muted","No modem"]
},

async loadGnss(first=false){

    const res=await fetch("/api/gnss");

    if(!res.ok)return;

    const data=await res.json();

    if(first){
        gnssEnabledInput.value=String(data.enabled);
        gnssClockInput.value=String(data.set_clock);
        gnssTrackInput.value=String(data.track_days);
        ["gnssEnabledInput","gnssClockInput","gnssTrackInput"].forEach(id=>ChaosSelect.refresh(document.getElementById(id)));
    }

    this.lastGnss=data;

    this.renderGnss(data);

},

renderGnss(data){

    const [badge,label]=this.GNSS_LEVELS[data.level]||["muted",data.level];

    gnssPill.className=`status ${badge}`;
    gnssPill.textContent=label;
    gnssMessage.textContent=data.message;

    const p=data.position;

    gnssPosition.textContent=p?`${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}`:"—";
    gnssMapRow.classList.toggle("hidden",!p);

    this.mapView?.setPosition(p||null);
    gnssCenter.classList.toggle("hidden",!p||!this.mapView);

    if(p){
        gnssOsm.href=`https://www.openstreetmap.org/?mlat=${p.latitude}&mlon=${p.longitude}#map=16/${p.latitude}/${p.longitude}`;
        gnssCopy.onclick=async()=>{
            try{
                await navigator.clipboard.writeText(`${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}`);
                gnssStatus.textContent="Position copied.";
            }catch{
                gnssStatus.textContent=`${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}`;
            }
        };
    }

    gnssAccuracy.textContent=p?.accuracy_m?`about ${p.accuracy_m} m (${p.fix_type})`:p?p.fix_type:"—";
    gnssAltitude.textContent=p?.altitude!=null?`${Math.round(p.altitude)} m`:"—";
    gnssSpeed.textContent=p?.speed_kmh!=null
        ?`${p.speed_kmh < 1 ? "standing" : `${Math.round(p.speed_kmh)} km/h`}${p.course!=null&&p.speed_kmh>=1?`, heading ${Math.round(p.course)}°`:""}`
        :"—";

    if(data.constellations.length){
        const parts=data.constellations.map(c=>`${c.name} ${c.heard}/${c.in_view}`);
        gnssSatellites.textContent=`${data.used!=null?`${data.used} used · `:""}heard/in view: ${parts.join(", ")}`;
    }else{
        gnssSatellites.textContent="—";
    }

    const cell=data.cell;

    gnssCell.textContent=cell&&cell.mcc
        ?`${cell.mcc}-${cell.mnc}${cell.tac?` · area ${cell.tac}`:cell.lac?` · area ${cell.lac}`:""}${cell.cid?` · cell ${cell.cid}`:""}`
        :"—";

    gnssClock.textContent=data.clock_synchronized===true
        ?"Synchronised over the internet"
        :data.clock_synchronized===false
            ?"Not synchronised yet: GNSS sets it with the next position"
            :data.set_clock?"—":"Internet time only";

},

async saveGnss(){

    gnssSave.disabled=true;
    gnssStatus.textContent="Saving...";

    const res=await fetch("/api/gnss/settings",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            enabled:gnssEnabledInput.value==="true",
            set_clock:gnssClockInput.value==="true",
            track_days:Number(gnssTrackInput.value)
        })
    });

    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    gnssSave.disabled=false;
    gnssStatus.textContent=data.success?`✓ ${data.message}`:data.message||"Failed to save.";

    if(data.status)this.renderGnss(data.status);

    this.loadTrack();

},

// ------------------------------------------------------------
// Map
// ------------------------------------------------------------

/** MapLibre only once the card is on screen: it is a big download. */
watchMap(){

    const start=()=>this.openMap();

    if(!("IntersectionObserver" in window))return start();

    this.mapObserver=new IntersectionObserver(entries=>{
        if(entries.some(e=>e.isIntersecting)){
            this.mapObserver.disconnect();
            start();
        }
    },{rootMargin:"200px"});

    this.mapObserver.observe(gnssMap);

},

async openMap(){

    const region=this.maps?.state==="ready"?this.maps.region:null;

    try{

        if(!window.ChaosMap)await this.loadScript("/static/js/map.js");

        this.mapView?.destroy();
        this.mapView=await ChaosMap.create(gnssMap,{region});

        if(this.lastGnss)this.renderGnss(this.lastGnss);

        this.loadTrack();

    }catch(error){

        gnssMap.textContent=`The map could not load: ${error.message}`;

    }

},

loadScript(src){

    return new Promise((resolve,reject)=>{
        const script=document.createElement("script");
        script.src=src;
        script.onload=resolve;
        script.onerror=()=>reject(new Error(src));
        document.head.appendChild(script);
    });

},

async loadTrack(){

    if(!this.mapView)return;

    const res=await fetch("/api/gnss/track");

    if(res.ok)this.mapView.setTrack((await res.json()).points);

},

async clearTrack(){

    if(!await ChaosModal.confirm({
        title:"Clear the track?",
        subtitle:"Every position recorded so far is deleted from the router.",
        confirmText:"Clear"
    }))return;

    const res=await fetch("/api/gnss/track/clear",{method:"POST"});
    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    gnssStatus.textContent=data.success?`✓ ${data.message}`:data.message;

    this.loadTrack();

},

// ------------------------------------------------------------
// Detailed map download
// ------------------------------------------------------------

formatSize(bytes){

    if(bytes==null)return "--";
    if(bytes>=1e9)return `${(bytes/1e9).toFixed(1)} GB`;

    return `${Math.round(bytes/1e6)} MB`;

},

async loadMaps(){

    const res=await fetch("/api/maps");

    if(!res.ok)return;

    const data=await res.json();
    const before=this.maps?.state;

    this.maps=data;

    // The list once (again when the router's country becomes known),
    // that country first, unless a choice was made already.
    if(!mapsCountry.options.length||(data.here&&data.here!==this.mapsHere&&!this.mapsChosen)){

        this.mapsHere=data.here;

        const here=data.countries.find(c=>c.code===data.here);
        const list=here?[here,...data.countries.filter(c=>c!==here)]:data.countries;

        mapsCountry.replaceChildren(...list.map(c=>new Option(c===here?`${c.name} (where the router is)`:c.name,c.code)));

        const chosen=data.region?.code||data.here;

        if(chosen)mapsCountry.value=chosen;

        ChaosSelect.refresh(mapsCountry);

    }

    this.renderMaps(data);

    if(before==="downloading"&&data.state==="ready")mapsStatus.textContent="✓ Map downloaded. The map now shows it.";

    // Finished or deleted: the map switches its data.
    if(before&&before!==data.state&&(data.state==="ready"||before==="ready")&&this.mapView)this.openMap();

},

renderMaps(data){

    const downloading=data.state==="downloading";

    mapsDownload.classList.toggle("hidden",downloading);
    mapsCancel.classList.toggle("hidden",!downloading);
    mapsDelete.classList.toggle("hidden",data.state!=="ready");
    mapsChoice.classList.toggle("hidden",downloading);

    mapsDownload.textContent=data.state==="ready"?"Download another country":"Download map";

    const free=data.free!=null?` ${this.formatSize(data.free)} free on the router.`:"";

    if(data.state==="ready"){
        const r=data.region;
        mapsInfo.textContent=`${r.name}, ${data.details[r.maxzoom]?.toLowerCase()||`zoom ${r.maxzoom}`}: ${this.formatSize(data.size)}, OpenStreetMap of ${r.build.replace(".pmtiles","")}.${free}`;
    }else if(downloading){
        mapsInfo.textContent=data.stage==="tool"
            ?`Preparing the download of ${data.country} (fetching the pmtiles tool and the list of builds)...`
            :data.downloaded
                ?`Downloading ${data.country}: ${this.formatSize(data.downloaded)} so far, ${Math.floor(data.seconds/60)} min.${free}`
                :`Downloading ${data.country}, ${Math.floor(data.seconds/60)} min so far (the tool's progress is below).${free}`;
    }else{
        mapsInfo.textContent=`None yet: the map shows the countries of the world only.${free}`;
    }

    // A failed download keeps the map there was: the error shows either way.
    if(data.error&&!downloading)mapsStatus.textContent=`The last download failed: ${data.error}`;

    mapsLog.textContent=data.log||"";
    mapsLog.classList.toggle("hidden",!(downloading||data.state==="failed")||!data.log);

},

async mapsAction(url,body){

    const res=await fetch(url,{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(body||{})
    });

    const data=await res.json().catch(()=>({message:"Unexpected server response."}));

    mapsStatus.textContent=data.success?`✓ ${data.message}`:data.message;

    await this.loadMaps();

},

async downloadMap(){

    const name=mapsCountry.selectedOptions[0]?.textContent||"this country";

    if(!await ChaosModal.confirm({
        title:`Download the map of ${name.replace(" (where the router is)","")}?`,
        subtitle:"This can take a while and uses the router's internet (mobile data counts towards your limit). A detailed map you already have is replaced when the new one is done.",
        confirmText:"Download"
    }))return;

    this.mapsAction("/api/maps/download",{country:mapsCountry.value,maxzoom:Number(mapsDetail.value)});

},

init(){

    ["mapsCountry","mapsDetail","gnssTrackInput"].forEach(id=>ChaosSelect.enhance(document.getElementById(id)));

    mapsDownload.onclick=()=>this.downloadMap();
    mapsCountry.addEventListener("change",()=>{ this.mapsChosen=true; });
    mapsCancel.onclick=()=>this.mapsAction("/api/maps/cancel");
    mapsDelete.onclick=async()=>{
        if(await ChaosModal.confirm({title:"Delete the detailed map?",subtitle:"The map shows the countries of the world only again.",confirmText:"Delete"}))
            this.mapsAction("/api/maps/delete");
    };

    gnssTrackClear.onclick=()=>this.clearTrack();
    gnssCenter.onclick=()=>this.mapView?.center();

    this.loadMaps().then(()=>this.watchMap());
    this.mapsTimer=Poll.every(5000,()=>this.loadMaps());
    this.trackTimer=Poll.every(60000,()=>this.loadTrack());


    ["gnssEnabledInput","gnssClockInput"].forEach(id=>ChaosSelect.enhance(document.getElementById(id)));

    gnssSave.onclick=()=>this.saveGnss();

    this.loadGnss(true).then(()=>{
        if(window.routeParam?.("show")==="gnss")gnssCard.scrollIntoView({block:"start",behavior:"smooth"});
    });

    this.gnssTimer=Poll.every(5000,()=>this.loadGnss());

    ["mobileEnabledInput","mobileRoamingInput"].forEach(id=>ChaosSelect.enhance(document.getElementById(id)));

    for(let day=1;day<=28;day++){
        dataDayInput.add(new Option(day===1?"1 (start of the month)":String(day),String(day)));
    }

    ["dataDayInput","dataWarnInput","dataBlockInput"].forEach(id=>ChaosSelect.enhance(document.getElementById(id)));

    dataLimitSave.onclick=()=>this.saveUsage();
    dataUsedSave.onclick=()=>this.correctUsage(false);
    dataUsedReset.onclick=()=>this.correctUsage(true);
    dataLimitInput.onkeydown=event=>{ if(event.key==="Enter")this.saveUsage(); };
    dataUsedInput.onkeydown=event=>{ if(event.key==="Enter")this.correctUsage(false); };

    this.loadUsage().then(()=>{
        // From the dashboard's "Data limit" link.
        if(window.routeParam?.("show")==="data-limit"){
            dataLimitCard.scrollIntoView({block:"start",behavior:"smooth"});
            dataLimitInput.focus({preventScroll:true});
        }
    });

    this.usageTimer=Poll.every(30000,async()=>{
        const res=await fetch("/api/data-usage");
        if(res.ok)this.renderUsage(await res.json());
    });

    modemSwitchQmi.onclick=()=>this.switchQmi();

    simPinUnlock.onclick=()=>this.unlock();
    simPinInput.onkeydown=event=>{ if(event.key==="Enter")this.unlock(); };

    mobileSave.onclick=()=>this.saveMobile();
    mobileForgetPin.onclick=()=>this.saveMobile({forget_pin:true});

    this.loadMobile();
    this.update();
    this.timer=Poll.every(3000,()=>this.update());

},

destroy(){

    Poll.stop(this.timer);
    Poll.stop(this.usageTimer);
    Poll.stop(this.gnssTimer);
    Poll.stop(this.mapsTimer);
    Poll.stop(this.trackTimer);

    this.mapObserver?.disconnect();
    this.mapView?.destroy();
    this.mapView=null;
    this.maps=null;
    this.mapsHere=null;
    this.mapsChosen=false;

}

};
