'use strict';
(async()=>{
  if(new URL(location.href).searchParams.has('invite'))history.replaceState(null,'','/');
  const $=id=>document.getElementById(id),form=$('phone-form'),status=$('phone-status');
  try {
    const response=await fetch('/assets/maps/satellite.json');if(!response.ok)throw Error('Saved map missing');
    const metadata=await response.json();
    const picker=SanjayLocationPicker.create('phone-map',metadata,(lat,lon)=>{form.elements.lat.value=lat.toFixed(6);form.elements.lon.value=lon.toFixed(6);status.textContent='Pin confirmed. Check this is the reported location.';},()=>{form.elements.lat.value='';form.elements.lon.value='';},status,form);
    $('phone-centre').onclick=picker.confirm;
    $('phone-gps').onclick=()=>{if(!window.isSecureContext||!navigator.geolocation){status.textContent='GPS unavailable on plain LAN HTTP. Drag map or enter coordinates.';return;}navigator.geolocation.getCurrentPosition(p=>{picker.setLocation(p.coords.latitude,p.coords.longitude);},()=>{status.textContent='GPS denied/unavailable. Drag map instead.';},{timeout:10000});};
  }catch(error){status.textContent=error.message+' Manual coordinates remain available.';}
    form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('button');button.disabled=true;
      try {const fields=Object.fromEntries(new FormData(form));fields.lat=Number(fields.lat);fields.lon=Number(fields.lon);
        const r=await fetch('/api/reports',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('[name=csrf-token]').content,'X-Form-Ticket':document.querySelector('[name=form-ticket]').content},body:JSON.stringify(fields)});const result=await r.json();if(!r.ok)throw Error(result.error||'Submission failed');
        status.textContent=`Saved ${result.id}. Status: unverified; operator review pending.`;form.hidden=true;const again=document.createElement('a');again.href='/';again.textContent='Report another problem';status.append(document.createElement('br'),again);
      }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
    };
})();
