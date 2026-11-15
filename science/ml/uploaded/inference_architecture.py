import torch
from torch import nn
import torch.nn.functional as F

def block(a,b): return nn.Sequential(nn.Conv2d(a,b,3,padding=1,bias=False),nn.GroupNorm(4,b),nn.SiLU())
class ResidualDW(nn.Module):
    def __init__(self,a,b,stride=1):
        super().__init__(); e=max(b,((int(1.5*a)+7)//8)*8)
        self.body=nn.Sequential(nn.Conv2d(a,e,1,bias=False),nn.GroupNorm(4,e),nn.SiLU(),nn.Conv2d(e,e,3,stride,1,groups=e,bias=False),nn.GroupNorm(4,e),nn.SiLU(),nn.Conv2d(e,b,1,bias=False),nn.GroupNorm(4,b))
        self.skip=nn.Identity() if a==b and stride==1 else nn.Conv2d(a,b,1,stride,bias=False)
    def forward(self,x):return F.silu(self.body(x)+self.skip(x))
class LocalTemporal(nn.Module):
    def __init__(self,c=48,d=16,v=24,r=2):
        super().__init__(); self.r=r; self.k=2*r+1; self.q=nn.Conv2d(c,d,1); self.key=nn.Conv2d(c,d,1); self.value=nn.Conv2d(c,v,1); self.out=nn.Conv2d(v,c,1,bias=False); self.gate=nn.Conv2d(2*c+1,c,1); self.beta=nn.Parameter(torch.zeros(())); self.null=nn.Parameter(torch.zeros(()))
        yy,xx=torch.meshgrid(torch.arange(-r,r+1),torch.arange(-r,r+1),indexing='ij'); self.register_buffer('dist',(xx.square()+yy.square()).float().flatten()/r**2)
    def forward(self,cur,prev,valid):
        b,_,h,w=cur.shape; k=self.k**2; N=h*w
        q=F.normalize(self.q(cur),dim=1,eps=1e-6).reshape(b,-1,1,N); keys=F.normalize(self.key(prev),dim=1,eps=1e-6); vals=self.value(prev)
        unfold=lambda x:F.unfold(x,self.k,padding=self.r).reshape(b,x.shape[1],k,N)
        good=unfold(valid)>0.5; score=(q*unfold(keys)).sum(1)-F.softplus(self.beta)*self.dist[None,:,None]; score=score.masked_fill(~good[:,0],-1e4)
        prob=torch.cat([score,self.null.expand(b,1,N)],1).softmax(1); rho=(1-prob[:,-1:]).reshape(b,1,h,w)*valid
        agg=self.out((unfold(vals)*prob[:,:k,None,:].transpose(1,2)).sum(2).reshape(b,-1,h,w))
        gate=torch.sigmoid(self.gate(torch.cat([cur,agg,rho],1))); return cur+gate*rho*agg
class ImaginationSky(nn.Module):
    def __init__(self,attention=True,sobel=True):
        super().__init__(); self.attention=attention; self.sobel=sobel
        K=torch.tensor([[-1.,0,1],[-2,0,2],[-1,0,1]])/8; self.register_buffer('sobel_kernel',torch.stack([K,K.T])[:,None])
        self.stem=block(4,16); self.enc1=ResidualDW(16,24,2); self.enc2=ResidualDW(24,48,2); self.temporal=LocalTemporal(); self.physics=block(2,16)
        self.dec2=ResidualDW(48*4+16,48); self.dec1=ResidualDW(48+48,32); self.dec0=ResidualDW(32+32,24); self.head=nn.Conv2d(24,6,1)
        # Motion-specific branch avoids shared Sobel gradients entirely. Raw signed evidence
        # and multi-scale smoothed differences supply a direct physical shortcut.
        self.motion=nn.Sequential(block(8,24),ResidualDW(24,32),ResidualDW(32,32),nn.Conv2d(32,2,1))
    def encode(self,x):
        g=F.conv2d(F.pad(x,(1,1,1,1),mode='replicate'),self.sobel_kernel); g=torch.cat([g,(g.square().sum(1,keepdim=True)+1e-8).sqrt()],1)
        if not self.sobel:g=torch.zeros_like(g)
        a=self.stem(torch.cat([x,g],1)); b=self.enc1(a); return a,b,self.enc2(b)
    def forward(self,x):
        a0,a1,a2=self.encode(x[:,0:1]); b0,b1,b2=self.encode(x[:,1:2]); v=F.avg_pool2d(x[:,3:4],4,4)>.99; v=v.to(x.dtype)
        aa=self.temporal(a2,b2,v) if self.attention else a2; bb=self.temporal(b2,a2,v) if self.attention else b2
        physics=F.interpolate(self.physics(x[:,2:4]),size=a2.shape[-2:],mode='bilinear',align_corners=False)
        z=self.dec2(torch.cat([a2,b2,aa,bb,physics],1)); z=self.dec1(torch.cat([F.interpolate(z,size=a1.shape[-2:],mode='bilinear',align_corners=False),a1,b1],1)); z=self.dec0(torch.cat([F.interpolate(z,size=a0.shape[-2:],mode='bilinear',align_corners=False),a0,b0],1)); base=self.head(z)
        d=x[:,2:3]; v=x[:,3:4]; smooth=lambda k:F.avg_pool2d(d*v,k,1,k//2)/F.avg_pool2d(v,k,1,k//2).clamp_min(.1)
        d3=smooth(3); d5=smooth(5)
        m=self.motion(torch.cat([x[:,0:2],F.relu(-d),F.relu(d),d3,d5,v,torch.abs(d3)],1))
        return torch.cat([base[:,:2],m,base[:,4:]],1)


class PairHead(nn.Module):
    def __init__(self):
        super().__init__(); self.layers=nn.Sequential(nn.Linear(15,48),nn.LayerNorm(48),nn.SiLU(),nn.Linear(48,24),nn.SiLU(),nn.Linear(24,1))
    def forward(self,x):return self.layers(x).squeeze(-1)

