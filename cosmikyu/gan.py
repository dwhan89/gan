from cosmikyu import config
import numpy as np
import os

from torchvision.utils import save_image
from torch.autograd import Variable
import torch.autograd as autograd

import torch.nn as nn
import torch

import mlflow
import mlflow.pytorch

from cosmikyu.model import DCGAN_SIMPLE_Generator, DCGAN_SIMPLE_Discriminator, DCGAN_Generator, DCGAN_Discriminator, \
    WGAN_Generator, WGAN_Discriminator


class GAN(object):
    def __init__(self, identifier, shape, latent_dim, p_fliplabel=0., output_path=None, experiment_path=None,
                 cuda=False, ngpu=1):
        self.cuda = cuda
        self.ngpu = 0 if not self.cuda else ngpu
        self.shape = shape
        self.latent_dim = latent_dim
        self.identifier = identifier
        self.p_fliplabel = p_fliplabel

        self.output_path = output_path or os.path.join(config.default_output_dir)
        self.tracking_path = os.path.join(self.output_path, "mlruns")
        self.experiment_path = experiment_path or os.path.join(self.output_path, identifier)
        mlflow.set_tracking_uri(self.tracking_path)
        self.experiment = mlflow.get_experiment_by_name(identifier) or mlflow.get_experiment(
            mlflow.create_experiment(identifier))

        if torch.cuda.is_available() and not self.cuda:
            print("[WARNING] You have a CUDA device. You probably want to run with CUDA enabled")
        self.device = torch.device("cuda:0" if self.cuda else "cpu")

        self.generator = None
        self.discriminator = None

        self.latent_vector_sampler = self._get_default_latent_vector_sampler()
        self.model_params = {"shape": shape, "latent_dim": latent_dim, "p_fliplabel": p_fliplabel, "sampler": "normal"}
        self.Tensor = torch.cuda.FloatTensor if self.cuda else torch.FloatTensor

    def load_states(self, output_path, postfix="", mlflow_run=None):
        saving_point_tracker_file = os.path.join(output_path, "saving_point.txt")
        if os.path.exists(saving_point_tracker_file) and not postfix:
            with open(saving_point_tracker_file, "r") as handle:
                postfix = handle.readline()
        generator_state_file = os.path.join(output_path, "generator{}.pt".format(postfix))
        discriminator_state_file = os.path.join(output_path, "discriminator{}.pt".format(postfix))
        try:
            print("loading saved states", postfix)
            if mlflow_run and False:
                self.generator = mlflow.pytorch.load_model(generator_state_file)
                self.discriminator = mlflow.pytorch.load_model(discriminator_state_file)
            else:
                self.generator.load_state_dict(torch.load(generator_state_file, map_location=self.device))
                self.discriminator.load_state_dict(torch.load(discriminator_state_file, map_location=self.device))
        except Exception:
            print("failed to load saved states")

    def save_states(self, output_path, postfix="", mlflow_run=None):
        postfix = "" if postfix == "" else "_{}".format(str(postfix))
        print("saving states", postfix)
        generator_state_file = os.path.join(output_path, "generator{}.pt".format(postfix))
        discriminator_state_file = os.path.join(output_path, "discriminator{}.pt".format(postfix))
        saving_point_tracker_file = os.path.join(output_path, "saving_point.txt")
        with open(saving_point_tracker_file, "w") as handle:
            handle.write(postfix)
        if mlflow_run and False:
            mlflow.pytorch.save_model(self.generator, generator_state_file)
            mlflow.pytorch.save_model(self.discriminator, discriminator_state_file)
        else:
            torch.save(self.generator.state_dict(), generator_state_file)
            torch.save(self.discriminator.state_dict(), discriminator_state_file)

    def _get_optimizers(self, **kwargs):
        raise NotImplemented()

    def _post_process_discriminator(self, **kwargs):
        pass

    def _eval_discriminator_loss(self, real_imgs, gen_imgs, **kwargs):
        raise NotImplemented()

    def _eval_generator_loss(self, real_imgs, gen_imgs):
        raise NotImplemented()

    def _get_latent_vector(self, nbatch, seed=None):
        if seed is not None:
            np.random.seed(seed)
        return Variable(self.Tensor(self.latent_vector_sampler(nbatch, self.latent_dim)))

    def _get_default_latent_vector_sampler(self):
        return lambda x, y: np.random.normal(0, 1, (x, y))

    def update_latent_vector_sampler(self, sampler):
        self.latent_vector_sampler = sampler

    def _flip_label(self):
        return np.random.uniform(0, 1) < self.p_fliplabel

    def generate_samples(self, nbatch, seed=None):
        z = self._get_latent_vector(nbatch, seed)
        return self.generator(z).detach()

    def train(self, dataloader, nepochs=200, ncritics=5, sample_interval=1000,
              save_interval=10000, load_states=True, save_states=True, verbose=True, mlflow_run=None, **kwargs):
        kwargs.update({"nepochs": nepochs, "ncritics": ncritics})
        kwargs.update(self.model_params)
        # Logging parameters
        if mlflow_run:
            for key, value in kwargs.items():
                mlflow.log_param(key, value)

        # Base Setup
        run_id = "trial" if not mlflow_run else mlflow_run.info.run_id
        run_path = os.path.join(self.experiment_path, run_id)
        artifacts_path = os.path.join(run_path, "artifacts")
        model_path = os.path.join(run_path, "model")

        os.makedirs(artifacts_path, exist_ok=True)
        os.makedirs(model_path, exist_ok=True)
        if load_states:
            self.load_states(model_path)

        self.save_states(model_path, 0)
        # Get Optimizers
        opt_gen, opt_disc = self._get_optimizers(**kwargs)
        batches_done = 0
        for epoch in range(nepochs):

            for i, sample in enumerate(dataloader):
                imgs = sample[0]
                real_imgs = Variable(imgs.type(self.Tensor))

                opt_disc.zero_grad()
                # Sample noise as generator input
                z = self._get_latent_vector(imgs.shape[0])

                # Generate a batch of images
                gen_imgs = self.generator(z).detach()

                # Adversarial loss 
                loss_D = self._eval_discriminator_loss(real_imgs, gen_imgs, **kwargs)
                mlflow.log_metric("D loss", loss_D.item())
                loss_D.backward()
                opt_disc.step()

                # Hook for Discriminator Post Processing
                self._post_process_discriminator(**kwargs)
                if i % ncritics == 0:
                    opt_gen.zero_grad()

                    # Generate a batch of images
                    gen_imgs = self.generator(z)
                    # Adversarial loss\
                    loss_G = self._eval_generator_loss(real_imgs, gen_imgs)
                    mlflow.log_metric("G loss", loss_G.item())

                    loss_G.backward()
                    opt_gen.step()
                    if verbose:
                        print("[Epoch %d/%d] [Batch %d/%d] [D loss: %f] [G loss: %f]"
                              % (epoch, nepochs, batches_done % len(dataloader), len(dataloader), loss_D.item(),
                                 loss_G.item())
                              )

                if batches_done % sample_interval == 0:
                    temp = torch.cat((real_imgs.data[:1], gen_imgs.data[:5]), 0)
                    temp = temp if gen_imgs.shape[-3] < 4 else torch.unsqueeze(torch.sum(temp, 1), 1)
                    save_image(temp, os.path.join(artifacts_path, "%d.png" % batches_done), normalize=True,
                               nrow=int(temp.shape[0] / 2.))
                batches_done += 1

            if int(epoch + 1) % save_interval == 0 and save_states:
                self.save_states(model_path, int(epoch + 1))
        if mlflow_run:
            mlflow.log_artifacts(artifacts_path)
        if save_states:
            self.save_states(model_path, nepochs)


class WGAN(GAN):
    def __init__(self, identifier, shape, latent_dim, p_fliplabel=0., output_path=None, experiment_path=None,
                 cuda=False, ngpu=1):
        super().__init__(identifier, shape, latent_dim, p_fliplabel=p_fliplabel, output_path=output_path,
                         experiment_path=experiment_path,
                         cuda=cuda, ngpu=ngpu)

        self.generator = WGAN_Generator(shape, latent_dim, ngpu=self.ngpu).to(device=self.device)
        self.discriminator = WGAN_Discriminator(shape, ngpu=self.ngpu).to(device=self.device)

    def _post_process_discriminator(self, **kwargs):
        clip_tresh = kwargs["clip_tresh"]
        # Clip weights of discriminator
        for p in self.discriminator.parameters():
            p.data.clamp_(-clip_tresh, clip_tresh)

    def _get_optimizers(self, **kwargs):
        opt_gen = torch.optim.RMSprop(self.generator.parameters(), lr=kwargs['lr'])
        opt_disc = torch.optim.RMSprop(self.discriminator.parameters(), lr=kwargs['lr'])
        return opt_gen, opt_disc

    def _eval_generator_loss(self, real_imgs, gen_imgs):
        return -torch.mean(self.discriminator(gen_imgs))

    def _eval_discriminator_loss(self, real_imgs, gen_imgs, **kwargs):
        ret = -torch.mean(self.discriminator(real_imgs)) + torch.mean(self.discriminator(gen_imgs))
        return -1 * ret if self._flip_label() else ret

    def train(self, dataloader, nepochs=200, ncritics=5, sample_interval=1000,
              save_interval=10000, load_states=True, save_states=True, verbose=True, mlflow_run=None, lr=0.00005,
              clip_tresh=0.01):
        super().train(dataloader, nepochs=nepochs, ncritics=ncritics, sample_interval=sample_interval,
                      save_interval=save_interval, load_states=load_states, save_states=save_states, verbose=verbose,
                      mlflow_run=mlflow_run,
                      lr=lr, clip_tresh=clip_tresh)


class WGAN_GP(GAN):
    def __init__(self, identifier, shape, latent_dim, p_fliplabel=0., output_path=None, experiment_path=None,
                 cuda=False, ngpu=1):
        super().__init__(identifier, shape, latent_dim, p_fliplabel=p_fliplabel, output_path=output_path,
                         experiment_path=experiment_path,
                         cuda=cuda, ngpu=ngpu)

        self.generator = WGAN_Generator(shape, latent_dim, ngpu=self.ngpu).to(device=self.device)
        self.discriminator = WGAN_Discriminator(shape, ngpu=self.ngpu).to(device=self.device)

    def _post_process_discriminator(self, **kwargs):
        pass

    def _get_optimizers(self, **kwargs):
        lr, betas = kwargs['lr'], kwargs["betas"]
        opt_gen = torch.optim.Adam(self.generator.parameters(), lr=lr, betas=betas)
        opt_disc = torch.optim.Adam(self.discriminator.parameters(), lr=lr, betas=betas)
        return opt_gen, opt_disc

    def _eval_generator_loss(self, real_imgs, gen_imgs):
        return -torch.mean(self.discriminator(gen_imgs))

    def _eval_discriminator_loss(self, real_imgs, gen_imgs, **kwargs):
        # determine the interpolation point 
        eps = self.Tensor(np.random.random((real_imgs.data.size(0), 1, 1, 1)))
        interp_data = (eps * real_imgs.data + ((1 - eps) * gen_imgs.data)).requires_grad_(True)
        disc_interp = self.discriminator(interp_data)
        storage = Variable(self.Tensor(real_imgs.data.shape[0], 1).fill_(1.0), requires_grad=False)
        # compute gradient w.r.t. interpolates
        gradients = autograd.grad(
            outputs=disc_interp,
            inputs=interp_data,
            grad_outputs=storage,
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
        )[0]
        gradients = gradients.view(gradients.size(0), -1)
        GP = ((gradients.norm(2, dim=1) - 1) ** 2).mean()
        ret = -torch.mean(self.discriminator(real_imgs)) + torch.mean(self.discriminator(gen_imgs)) + kwargs[
            'lambda_gp'] * GP
        return -1 * ret if self._flip_label() else ret

    def train(self, dataloader, nepochs=200, ncritics=5, sample_interval=1000,
              save_interval=10000, load_states=True, save_states=True, verbose=True, mlflow_run=None, lr=0.0002,
              betas=(0.5, 0.999), lambda_gp=10):
        super().train(dataloader, nepochs=nepochs, ncritics=ncritics, sample_interval=sample_interval,
                      save_interval=save_interval, load_states=load_states, save_states=save_states, verbose=verbose,
                      mlflow_run=mlflow_run,
                      lr=lr, betas=betas, lambda_gp=lambda_gp)


class DCGAN_SIMPLE(GAN):
    def __init__(self, identifier, shape, latent_dim, output_path=None, experiment_path=None, cuda=False, ngpu=1,
                 nconv_layer_gen=2, nconv_layer_disc=2, nconv_fcgen=32, nconv_fcdis=32):
        super().__init__(identifier, shape, latent_dim, p_fliplabel=0., output_path=output_path,
                         experiment_path=experiment_path,
                         cuda=cuda, ngpu=ngpu)

        self.nconv_layer_gen = nconv_layer_gen
        self.nconv_layer_disc = nconv_layer_disc
        self.nconv_fcgen = nconv_fcgen
        self.nconv_fcdis = nconv_fcdis

        self.model_params.update({"nconv_layer_gen": self.nconv_layer_gen, "nconv_layer_disc": self.nconv_layer_disc,
                                  "nconv_fcgen": self.nconv_fcgen, "nconv_fcdis": self.nconv_fcdis})

        self.generator = DCGAN_SIMPLE_Generator(shape, latent_dim, nconv_layer=self.nconv_layer_gen,
                                                nconv_fc=self.nconv_fcgen,
                                                ngpu=self.ngpu).to(device=self.device)
        self.discriminator = DCGAN_SIMPLE_Discriminator(shape, nconv_layer=self.nconv_layer_disc,
                                                        nconv_fc=self.nconv_fcdis,
                                                        ngpu=self.ngpu).to(device=self.device)

        # Initialize weights
        self.generator.apply(self._weights_init_normal)
        self.discriminator.apply(self._weights_init_normal)

        self.adversarial_loss = nn.BCELoss().to(device=self.device)

    def _weights_init_normal(self, layer):
        classname = layer.__class__.__name__
        if classname.find("Conv") != -1:
            nn.init.normal_(layer.weight.data, 0.0, 0.02)
        elif classname.find("BatchNorm2d") != -1:
            nn.init.normal_(layer.weight.data, 1.0, 0.02)
            nn.init.constant_(layer.bias.data, 0.0)

    def _post_process_discriminator(self, **kwargs):
        pass

    def _get_optimizers(self, **kwargs):
        lr, betas = kwargs['lr'], kwargs["betas"]
        opt_gen = torch.optim.Adam(self.generator.parameters(), lr=lr, betas=betas)
        opt_disc = torch.optim.Adam(self.discriminator.parameters(), lr=lr, betas=betas)
        return opt_gen, opt_disc

    def _eval_generator_loss(self, real_imgs, gen_imgs):
        valid = Variable(self.Tensor(real_imgs.shape[0], 1).fill_(1.0), requires_grad=False)
        return self.adversarial_loss(self.discriminator(gen_imgs), valid)

    def _eval_discriminator_loss(self, real_imgs, gen_imgs, **kwargs):
        valid = Variable(self.Tensor(real_imgs.shape[0], 1).fill_(1.0), requires_grad=False)
        fake = Variable(self.Tensor(real_imgs.shape[0], 1).fill_(0.0), requires_grad=False)
        labels = (fake, valid) if self._flip_label() else (valid, fake)

        real_loss = self.adversarial_loss(self.discriminator(real_imgs), labels[0])
        fake_loss = self.adversarial_loss(self.discriminator(gen_imgs), labels[1])
        return (real_loss + fake_loss) / 2

    def train(self, dataloader, nepochs=200, ncritics=5, sample_interval=1000,
              save_interval=5, load_states=True, save_states=True, verbose=True, mlflow_run=None, lr=0.0002,
              betas=(0.5, 0.999), **kwargs):

        super().train(dataloader, nepochs=nepochs, ncritics=ncritics, sample_interval=sample_interval,
                      save_interval=save_interval, load_states=load_states, save_states=save_states, verbose=verbose,
                      mlflow_run=mlflow_run,
                      lr=lr, betas=betas, **kwargs)


class DCGAN(DCGAN_SIMPLE):
    def __init__(self, identifier, shape, latent_dim, output_path=None, experiment_path=None, cuda=False, ngpu=1,
                 nconv_layer_gen=2, nconv_layer_disc=2, nconv_fcgen=32, nconv_fcdis=32, kernal_size=5, stride=2,
                 padding=2, output_padding=1, gen_act=nn.Tanh()):
        super().__init__(identifier, shape, latent_dim, output_path=output_path, experiment_path=experiment_path,
                         cuda=cuda, ngpu=ngpu, nconv_layer_gen=nconv_layer_gen, nconv_layer_disc=nconv_layer_disc,
                         nconv_fcgen=nconv_fcgen, nconv_fcdis=nconv_fcdis)

        self.model_params.update({"nconv_layer_gen": self.nconv_layer_gen, "nconv_layer_disc": self.nconv_layer_disc,
                                  "nconv_fcgen": self.nconv_fcgen, "nconv_fcdis": self.nconv_fcdis,
                                  "kernal_size": kernal_size,
                                  "stride": stride, "padding": padding, "output_padding": output_padding,
                                  "gen_act": gen_act.__class__.__name__})

        self.generator = DCGAN_Generator(shape, latent_dim, nconv_layer=self.nconv_layer_gen, nconv_fc=self.nconv_fcgen,
                                         ngpu=self.ngpu, kernal_size=kernal_size, stride=stride, padding=padding,
                                         output_padding=output_padding, activation=gen_act).to(device=self.device)
        self.discriminator = DCGAN_Discriminator(shape, nconv_layer=self.nconv_layer_disc, nconv_fc=self.nconv_fcdis,
                                                 ngpu=self.ngpu, kernal_size=kernal_size, stride=stride,
                                                 padding=padding).to(device=self.device)

        # Initialize weights
        self.generator.apply(self._weights_init_normal)
        self.discriminator.apply(self._weights_init_normal)


class DCGAN_WGP(DCGAN):
    def __init__(self, identifier, shape, latent_dim, output_path=None, experiment_path=None, cuda=False, ngpu=1,
                 nconv_layer_gen=2, nconv_layer_disc=2, nconv_fcgen=32, nconv_fcdis=32, kernal_size=5, stride=2,
                 padding=2, output_padding=1, gen_act=nn.Tanh()):
        super().__init__(identifier, shape, latent_dim, output_path=output_path, experiment_path=experiment_path,
                         cuda=cuda, ngpu=ngpu, nconv_layer_gen=nconv_layer_gen, nconv_layer_disc=nconv_layer_disc,
                         nconv_fcgen=nconv_fcgen, nconv_fcdis=nconv_fcdis, kernal_size=kernal_size, stride=stride,
                         padding=padding,
                         output_padding=output_padding, gen_act=gen_act)

        del self.discriminator
        self.discriminator = DCGAN_Discriminator(shape, nconv_layer=self.nconv_layer_disc, nconv_fc=self.nconv_fcdis,
                                                 ngpu=self.ngpu, kernal_size=kernal_size, stride=stride,
                                                 padding=padding, normalize=False).to(device=self.device)

        # Initialize weights
        self.discriminator.apply(self._weights_init_normal)
    
    def _eval_generator_loss(self, real_imgs, gen_imgs):
        return -torch.mean(self.discriminator(gen_imgs))

    def _eval_discriminator_loss(self, real_imgs, gen_imgs, **kwargs):
        # determine the interpolation point 
        eps = self.Tensor(np.random.random((real_imgs.data.size(0), 1, 1, 1)))
        interp_data = (eps * real_imgs.data + ((1 - eps) * gen_imgs.data)).requires_grad_(True)
        disc_interp = self.discriminator(interp_data)
        storage = Variable(self.Tensor(real_imgs.data.shape[0], 1).fill_(1.0), requires_grad=False)
        # compute gradient w.r.t. interpolates
        gradients = autograd.grad(
            outputs=disc_interp,
            inputs=interp_data,
            grad_outputs=storage,
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
        )[0]
        gradients = gradients.view(gradients.size(0), -1)
        GP = ((gradients.norm(2, dim=1) - 1) ** 2).mean()
        ret = -torch.mean(self.discriminator(real_imgs)) + torch.mean(self.discriminator(gen_imgs)) + kwargs[
            'lambda_gp'] * GP
        return -1 * ret if self._flip_label() else ret

    def train(self, dataloader, nepochs=200, ncritics=5, sample_interval=1000,
              save_interval=10000, load_states=True, save_states=True, verbose=True, mlflow_run=None, lr=0.0002,
              betas=(0.5, 0.999), lambda_gp=10):
        super().train(dataloader, nepochs=nepochs, ncritics=ncritics, sample_interval=sample_interval,
                      save_interval=save_interval, load_states=load_states, save_states=save_states, verbose=verbose,
                      mlflow_run=mlflow_run,
                      lr=lr, betas=betas, lambda_gp=lambda_gp)


class COSMOGAN(DCGAN):
    def __init__(self, identifier, shape, latent_dim, output_path=None, experiment_path=None,
                 cuda=False, nconv_fcgen=64,
                 nconv_fcdis=64, ngpu=1, gen_act=nn.Tanh()):
        super().__init__(identifier, shape, latent_dim, output_path=output_path,
                         experiment_path=experiment_path,
                         cuda=cuda, ngpu=ngpu, nconv_layer_gen=4, nconv_layer_disc=4,
                         nconv_fcgen=nconv_fcgen, nconv_fcdis=nconv_fcdis, kernal_size=5, stride=2, padding=2,
                         output_padding=1, gen_act=gen_act)


class COSMOGAN_WGP(DCGAN_WGP):
    def __init__(self, identifier, shape, latent_dim, output_path=None, experiment_path=None,
                 cuda=False, nconv_fcgen=64,
                 nconv_fcdis=64, ngpu=1, gen_act=nn.Tanh()):
        super().__init__(identifier, shape, latent_dim, output_path=output_path,
                         experiment_path=experiment_path,
                         cuda=cuda, ngpu=ngpu, nconv_layer_gen=4, nconv_layer_disc=4,
                         nconv_fcgen=nconv_fcgen, nconv_fcdis=nconv_fcdis, kernal_size=5, stride=2, padding=2,
                         output_padding=1, gen_act=gen_act)


