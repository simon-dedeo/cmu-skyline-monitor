; fig2a.pro — best-fit lightning emission model + camera channels, log-linear.
; Produces fig2a.eps (EPS, color). Run:  idl -e fig2a
pro fig2a
  compile_opt idl2

  ; ---- model ----------------------------------------------------------------
  nlam = 4000
  nm  = 380d + dindgen(nlam)*(720d - 380d)/(nlam-1)
  lam = nm*1d-9

  hh = 6.626d-34 & cc = 2.998d8 & kb = 1.381d-23
  planck = (2d*hh*cc^2/lam^5)/(exp(hh*cc/(lam*kb*30000d)) - 1d)

  ; atomic lines: [center nm, rel energy, sigma nm]
  atom = [[399.5d,0.45d,2.5d],[444.7d,0.30d,2.5d],[463.1d,0.40d,2.5d],$
          [486.1d,0.20d,2.5d],[500.5d,0.70d,2.5d],[568.0d,0.20d,2.5d],$
          [594.2d,0.10d,2.5d],[615.8d,0.10d,2.5d],[648.2d,0.15d,2.5d],$
          [656.3d,1.00d,3.0d]]
  mol  = [[391.4d,0.60d,3.0d],[427.8d,1.00d,3.0d],[470.9d,0.35d,3.0d]]

  la = dblarr(nlam) & lm = dblarr(nlam)
  for i=0,9 do la += atom[1,i]*exp(-0.5d*((nm-atom[0,i])/atom[2,i])^2)/atom[2,i]
  for i=0,2 do lm += mol[1,i]*exp(-0.5d*((nm-mol[0,i])/mol[2,i])^2)/mol[2,i]

  dl = lam[1]-lam[0]
  co = planck/total(planck*dl)          ; unit visible energy each
  la = la/total(la*dl)
  lm = lm/total(lm*dl)

  wc = 0.22d & wa = 0.66d & wm = 0.12d
  spec = wc*co + wa*la + wm*lm
  pk = max(spec)
  spec /= pk & cop = wc*co/pk & lmp = wm*lm/pk

  ; camera channels (Gaussian x IR-cut 670 / UV edge 398)
  cut = 1d/(1d + exp(-(670d0 - nm)/6d)) * (1d/(1d + exp(-(nm - 398d0)/5d)))
  chb = exp(-0.5d*((nm-455d)/38d)^2)*cut
  chg = exp(-0.5d*((nm-535d)/45d)^2)*cut
  chr = exp(-0.5d*((nm-612d)/48d)^2)*cut

  ; ---- plot -----------------------------------------------------------------
  set_plot, 'ps'
  device, filename='fig2a.eps', /encapsulated, /color, bits_per_pixel=8, $
          xsize=17, ysize=12, /helvetica
  !p.font = 0
  tvlct, [ 51,227,  0, 42, 74,150], $      ; 1 ink, 2 R, 3 G, 4 B, 5 violet, 6 grey
         [ 51, 73,131,120, 58,150], $
         [ 49, 72,  0,214,167,150], 1

  plot, nm, spec > 1d-4, /ylog, /nodata, $
        xrange=[380,720], yrange=[1d-3,100d], xstyle=1, ystyle=1, $
        xtitle='wavelength (nm)', ytitle='normalized intensity / sensitivity', $
        color=1, charsize=0.95, xthick=2, ythick=2, xticklen=0.02

  oplot, nm, chr > 1d-4, color=2, thick=3
  oplot, nm, chg > 1d-4, color=3, thick=3
  oplot, nm, chb > 1d-4, color=4, thick=3
  oplot, nm, cop > 1d-4, color=1, thick=2, linestyle=2
  oplot, nm, lmp > 1d-4, color=5, thick=3, linestyle=1
  oplot, nm, spec > 1d-4, color=5, thick=4

  ; ---- label every line -----------------------------------------------------
  ; [nm, staggered label height (log units above peak)]
  lbl  = ['N!D2!U+!N 391',  'N II 400',  'N!D2!U+!N 428', 'N II 445', $
          'N II 463', 'N!D2!U+!N 471', 'H!9b!X 486', 'N II 500', $
          'N II 568', 'N II 594', 'O I 616', 'N II 648', 'H!9a!X 656']
  lpos = [391.4, 399.5, 427.8, 444.7, 463.1, 470.9, 486.1, 500.5, $
          568.0, 594.2, 615.8, 648.2, 656.3]
  ; peak of the total model at each line center
  for i=0,12 do begin
    v  = spec[value_locate(nm, lpos[i])]
    yl = 2.0                                     ; common label baseline
    oplot, [lpos[i],lpos[i]], [v*1.12, yl*0.85], color=6, thick=1
    xyouts, lpos[i], yl, lbl[i], orientation=90, alignment=0, $
            charsize=0.72, color=1
  endfor

  ; legend (upper right, inside frame; bottom row anchored at data y = 10.5)
  x0=0.66
  leg = ['total model spectrum', '30 000 K Planck continuum', $
         'N!D2!U+!N 1N bands']
  lco = [5,1,5] & lls = [0,2,1] & lth = [4,2,3]
  yw = !y.window
  for i=0,2 do begin
    ydat  = 10.5d * 1.83d^(2-i)                  ; rows 35.2 / 19.2 / 10.5
    ynorm = yw[0] + (alog10(ydat/1d-3)/5d)*(yw[1]-yw[0])
    plots, x0+[0,0.045], ynorm+[0,0], /normal, color=lco[i], $
           linestyle=lls[i], thick=lth[i]
    xyouts, x0+0.058, ynorm-0.008, leg[i], /normal, charsize=0.78, color=1
  endfor

  device, /close
  set_plot, 'x'
  print, 'fig2a.eps written'
end
