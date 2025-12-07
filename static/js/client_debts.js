(function(){
  function qp(name){ try{ return new URLSearchParams(window.location.search).get(name); }catch(e){ return null; } }
  function formatCurrency(v){ try{ return new Intl.NumberFormat('fr-FR',{style:'currency',currency:'XOF',maximumFractionDigits:0}).format(v||0);}catch(e){return (v||0)+' XOF';} }
  function formatDate(s){ if(!s) return '-'; try{ const d=new Date(s); if(isNaN(d)) return '-'; return d.toLocaleDateString('fr-FR',{year:'numeric',month:'short',day:'numeric'});}catch(e){return '-';} }
  function escapeHtml(s){ return (s||'').replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

  async function loadData(){
    const cid = qp('client_id');
    if(!cid){ renderError('Client manquant.'); return; }
    try{
      const { data } = await axios.get(`/api/clients/${encodeURIComponent(cid)}/debts`);
      renderAll(data);
    }catch(e){
      renderError(e?.response?.data?.detail || 'Erreur lors du chargement');
    }
  }

  function renderError(msg){
    const root = document.getElementById('clientDebtsRoot');
    if(!root) return; root.innerHTML = `<div class="alert alert-danger">${escapeHtml(String(msg||''))}</div>`;
  }

  function renderAll(payload){
    if(!payload) return renderError('Données indisponibles');
    const { client, summary, invoices, manual_debts } = payload;
    const invoicesFiltered = (invoices||[]).filter(x => (x && x.status) !== 'paid');
    const manualDebtsFiltered = (manual_debts||[]).filter(x => (x && x.status) !== 'paid');

    const hdr = document.getElementById('clientHeader');
    if(hdr){
      hdr.innerHTML = `
        <div class="d-flex flex-wrap justify-content-between align-items-center">
          <div>
            <h2 class="h4 mb-1"><i class="bi bi-person me-2"></i>${escapeHtml(client?.name||'Client')}</h2>
            <div class="text-muted small">${escapeHtml(client?.phone||'')} ${client?.email? ' • '+escapeHtml(client.email): ''}</div>
          </div>
          <div class="d-flex gap-2">
            <a class="btn btn-outline-secondary" href="/clients/detail?id=${encodeURIComponent(client.client_id)}"><i class="bi bi-box-arrow-up-right me-1"></i>Fiche client</a>
            <button class="btn btn-primary" id="printBtn"><i class="bi bi-printer me-1"></i>Imprimer le récapitulatif</button>
          </div>
        </div>`;
    }

    const cards = document.getElementById('summaryCards');
    if(cards){
      cards.innerHTML = `
        <div class="col-md-3 mb-3"><div class="card text-white debt-client"><div class="card-body"><div class="d-flex justify-content-between"><div><h4 class="mb-0">${formatCurrency(summary?.total_amount||0)}</h4><p class="mb-0">Montant total</p></div><i class="bi bi-cash-coin display-6"></i></div></div></div></div>
        <div class="col-md-3 mb-3"><div class="card text-white debt-paid"><div class="card-body"><div class="d-flex justify-content-between"><div><h4 class="mb-0">${formatCurrency(summary?.total_paid||0)}</h4><p class="mb-0">Total payé</p></div><i class="bi bi-check2-circle display-6"></i></div></div></div></div>
        <div class="col-md-3 mb-3"><div class="card text-white debt-remaining"><div class="card-body"><div class="d-flex justify-content-between"><div><h4 class="mb-0">${formatCurrency(summary?.total_remaining||0)}</h4><p class="mb-0">Solde restant</p></div><i class="bi bi-exclamation-triangle display-6"></i></div></div></div></div>
        <div class="col-md-3 mb-3"><div class="card text-white debt-supplier"><div class="card-body"><div class="d-flex justify-content-between"><div><h4 class="mb-0">${parseInt(summary?.overdue_count||0)}</h4><p class="mb-0">Échéances dépassées</p></div><i class="bi bi-alarm display-6"></i></div></div></div></div>`;
    }

    const invTbody = document.getElementById('invoicesTbody');
    if(invTbody){
      const rows = (invoicesFiltered).map(inv=>{
        const items = (inv.items||[]).map(it=>`${escapeHtml(it.product_name)} × ${it.quantity} — ${formatCurrency(it.total)}`).join('<br>');
        const statusBadge = badgeFor(inv.status);
        return `<tr>
          <td><strong>${escapeHtml(inv.invoice_number||String(inv.id))}</strong></td>
          <td>${formatDate(inv.date)}</td>
          <td>${inv.due_date? formatDate(inv.due_date): '-'}</td>
          <td>${formatCurrency(inv.amount)}</td>
          <td class="text-success">${formatCurrency(inv.paid_amount||0)}</td>
          <td class="text-${(inv.remaining_amount||0)>0?'danger':'success'}">${formatCurrency(inv.remaining_amount||0)}</td>
          <td>${statusBadge}</td>
          <td class="small">${items||'-'}</td>
        </tr>`;
      }).join('');
      invTbody.innerHTML = rows || `<tr><td colspan="8" class="text-center text-muted py-3">Aucune facture en attente</td></tr>`;
    }

    const mdTbody = document.getElementById('manualDebtsTbody');
    if(mdTbody){
      const rows = (manualDebtsFiltered).map(d=>{
        const statusBadge = badgeFor(d.status);
        return `<tr>
          <td><strong>${escapeHtml(d.reference||String(d.id))}</strong></td>
          <td>${formatDate(d.date)}</td>
          <td>${d.due_date? formatDate(d.due_date): '-'}</td>
          <td>${formatCurrency(d.amount)}</td>
          <td class="text-success">${formatCurrency(d.paid_amount||0)}</td>
          <td class="text-${(d.remaining_amount||0)>0?'danger':'success'}">${formatCurrency(d.remaining_amount||0)}</td>
          <td>${statusBadge}</td>
          <td class="small">${escapeHtml(d.description||'-')}</td>
        </tr>`;
      }).join('');
      mdTbody.innerHTML = rows || `<tr><td colspan="8" class="text-center text-muted py-3">Aucune créance manuelle en attente</td></tr>`;
    }

    const printBtn = document.getElementById('printBtn');
    if(printBtn){
      printBtn.addEventListener('click', function(){
        const src = `/clients/debts/print/${encodeURIComponent(client.client_id)}`;
        const iframe = document.createElement('iframe');
        iframe.style.position = 'fixed';
        iframe.style.right = '0';
        iframe.style.bottom = '0';
        iframe.style.width = '0';
        iframe.style.height = '0';
        iframe.style.border = '0';
        iframe.src = src;
        iframe.onload = function(){
          try { iframe.contentWindow?.focus(); iframe.contentWindow?.print(); } catch(e){}
          setTimeout(()=>{ try{ iframe.remove(); }catch(_){} }, 2000);
        };
        document.body.appendChild(iframe);
      });
    }
  }

  function badgeFor(st){
    const map = {paid:'bg-success', partial:'bg-warning text-dark', overdue:'bg-danger', pending:'bg-secondary'};
    const label = {paid:'Payé', partial:'Partiel', overdue:'En retard', pending:'En attente'};
    const cls = map[st]||'bg-secondary';
    const txt = label[st]||st;
    return `<span class="badge ${cls}">${txt}</span>`;
  }

  document.addEventListener('DOMContentLoaded', loadData);
})();
