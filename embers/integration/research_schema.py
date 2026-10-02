"""Human-readable descriptions only. Rust alone validates the values."""
def schema(config, policy):
    rows=[]
    def add(path,symbol,name,range,subsystem,equation,effect,change='future queries; no restart'):
        tree=policy if path.startswith('policy.') else config
        parts=path.split('.')[1:]
        for p in parts:tree=tree[p]
        rows.append(dict(path=path,symbol=symbol,name=name,default=tree,valid_range=range,subsystem=subsystem,equation=equation,effect=effect,status='experimental',change_effect=change))
    for path,symbol,name,range,effect in [
        ('u0','u₀','Usefulness prior','[0,1]','Usefulness before effective evidence.'),
        ('kappa_u','κU','Usefulness prior strength','> 0','More prior mass makes evidence change usefulness more slowly.'),
        ('severity.contributed','s+','Positive evidence mass','> 0','Mass assigned to one accepted helpful experience.'),
        ('severity.irrelevant','s−','Irrelevant evidence mass','> 0','Mass assigned to one accepted irrelevant experience.'),
        ('severity.misleading','sM','Misleading evidence mass','> irrelevant mass','Stronger negative evidence for a misleading experience.'),
        ('cluster.session_window','Δsession','Session grouping window','[0,86400] seconds','Structural grouping for future reports in one session; 0 disables grouping.'),
        ('cluster.time_window','Δtime','No-session grouping window','[0,86400] seconds','Structural grouping for future reports without a session; 0 disables grouping.'),
        ('w0','w₀','Directional pair prior','[0,1]','Pair usefulness prior. W does not influence direct retrieval/LADC admission. After primary direct selection, Pairing Matrix V1 may use contextual directional W to select at most one paired memory.'),
        ('kappa_w','κW','Directional pair prior strength','> 0','Prior mass for directional pair evidence.')]:
        add('policy.'+path,symbol,name,range,'Pair evidence' if path in ('w0','kappa_w') else 'Feedback','(κ × prior + positive mass) / (κ + effective mass)',effect,'reprojects accepted evidence; grouping windows affect future grouping only; no restart')
    for key,symbol,name,range,equation,effect in [
        ('alpha','α','Usefulness modulation','0 ≤ α < computed recovery limit < 1','Q′ = Q[1 + α(2U−1)]','Strength of usefulness influence while query pressure remains necessary.'),
        ('epsilon_a','εA','Activation floor','0 < εA < .5; frozen once activation exists','A* = εA + (1−2εA)u/(u+v)','Lower bound; changing it after activation requires a separate explicit migration.'),
        ('rho','ρ','Reaction speed','> 0 per model unit','u=ρQ′; v=ρ[(1−d)+δ]','Controls how quickly activation approaches equilibrium.'),
        ('delta','δ','Relaxation baseline','> 0; recovery check also applies','v=ρ[(1−d)+δ]','Ensures relaxation even for high direct relevance.'),
        ('threshold','T','Active threshold','εA < T < worst-case strong-cue equilibrium','ACTIVE ⇔ activation ≥ T','Boundary between ACTIVE and LATENT labels.'),
        ('q_min','Qmin','Declared strong query','0 < Qmin ≤ 1','Qmin(1−α) > r/(1−r)[1−dmin+δ]','Minimum sustained query covered by the recovery guarantee.'),
        ('d_min','dmin','Declared strong direct relevance','[0,1]; recovery check also applies','r=(T−εA)/(1−2εA)','Minimum direct relevance covered by that guarantee.'),
        ('max_elapsed','Δt max','Maximum time step','> 0 model units','A_next=A*+(A−A*)exp[−(u+v)Δt]','Reject larger requested steps instead of silently clamping them.')]:
        add('config.'+key,symbol,name,range,'Query modulation' if key=='alpha' else 'Activation',equation,effect)
    for key,name,cap in [('max_seeds','Candidate memories',512),('max_state_memories','Activation cache memories',100000),('max_results','Returned memories',100),('token_budget','Total rendered token budget',1000000),('memory_token_cap','Per-memory token cap',1000000),('neighborhood_token_cap','Per-neighborhood token cap',1000000)]:
        add('config.'+key,key,name,f'integer 1..{cap}; compatible with total/candidate limits','Attention','exact tokenizer count ≤ configured cap','Limits admission. W does not influence direct retrieval/LADC admission. Pairing Matrix V1 attempts at most one contextual directional W-selected addition after primary direct selection, within existing capacity.')
    return rows
