from env import Env
from buffer import Memory
import numpy as np
import argparse 
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions.beta import Beta

class Model(nn.Module):
    def __init__(self, obs_dim, act_dim, save_dir="./ppo_model"):
        super(Model, self).__init__()
        self.cnn_base = nn.Sequential(  
            nn.Conv2d(obs_dim, 32, kernel_size=8, stride=4),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=4, stride=2),
            nn.ReLU(), 
            nn.Conv2d(64, 64, kernel_size=3, stride=1),  
            nn.ReLU(),  
            nn.Flatten(), 
        )
        self.v = nn.Sequential(nn.Linear(3136, 512), nn.ReLU(), nn.Linear(512, 1))
        self.fc = nn.Sequential(nn.Linear(3136, 512), nn.ReLU())
        self.alpha_head = nn.Sequential(nn.Linear(512, act_dim), nn.Softplus())
        self.beta_head = nn.Sequential(nn.Linear(512, act_dim),nn.Softplus())

        self.apply(self.weights)
        self.ckpt_file = save_dir + ".pth"

    @staticmethod
    def weights(m):
        if isinstance(m, nn.Conv2d):
            nn.init.xavier_uniform_(m.weight, gain=nn.init.calculate_gain('relu'))
            nn.init.constant_(m.bias, 0.1)

    def forward(self, x):
        x = self.cnn_base(x)
        v = self.v(x)
        x = self.fc(x)
        alpha = self.alpha_head(x) + 1
        beta = self.beta_head(x) + 1
        return (alpha, beta), v

    def save_ckpt(self):
        torch.save(self.state_dict(), self.ckpt_file)

    def load_ckpt(self, device):
        self.load_state_dict(torch.load(self.ckpt_file, map_location=device))

class Agent:
    def __init__(self, state_dim, action_dim, gamma=0.99, lamda=0.95, clip=0.1,
                 learning_rate=3e-4, batch_size=128, save_dir='./ppo_model',
                 epochs=8, device=torch.device("cuda" if torch.cuda.is_available() else "cpu")):

        self.gamma = gamma
        self.lamda = lamda
        self.clip = clip
        self.batch_size = batch_size
        self.epochs = epochs
        self.buffer = Memory()
        self.device = device
        self.model = Model(state_dim, action_dim, save_dir).to(device)
        self.optimizer = optim.Adam(self.model.parameters(), lr=learning_rate)
        
    def select_action(self, state):
        state = torch.tensor(state, dtype=torch.float).to(self.device).unsqueeze(0)
        with torch.no_grad():
            alpha, beta = self.model(state)[0]
            value = self.model(state)[1]
        dist = Beta(alpha, beta)
        action = dist.sample()
        logp = dist.log_prob(action).sum(dim=1)
        action = action.squeeze().cpu().numpy()
        logp = logp.item()
        return action, logp, value

    def memory(self, tranjectory):
        self.buffer.memory(*tranjectory)

    def save_model(self):
        print("... save model ...")
        self.model.save_ckpt()

    def load_model(self):
        print("... load model ...")
        self.model.load_ckpt(self.device)

    def learn(self):
        states, actions, probs, rewards, next_states, values, batchs = self.buffer.generate_batch(self.batch_size)
        
        s = torch.tensor(np.array(states), dtype=torch.float).to(self.device)
        a = torch.tensor(np.array(actions), dtype=torch.float).to(self.device)
        old_logp = torch.tensor(np.array(probs), dtype=torch.float).to(self.device)
        r = torch.tensor(np.array(rewards), dtype=torch.float).to(self.device)
        next_s = torch.tensor(np.array(next_states), dtype=torch.float).to(self.device)
        v = torch.tensor(values, dtype=torch.float).to(self.device)

        advantages = torch.zeros_like(v)

        for i in range((len(values)-1)):
            discount = 1
            a_t = 0
            for j in range(i, len(values)-1):
                a_t += discount*(r[j] + self.gamma*v[j+1] - v[j])
                discount *= self.gamma*self.lamda
            advantages[i] = a_t

        v_ = advantages + v  

        advantages = advantages.view(-1, 1)
        v_ = v_.view(-1, 1)

        for _ in range(self.epochs):
            for _ in batchs:
                
                random_index = np.random.randint(0, len(batchs))
                index = batchs[random_index]

                alpha, beta = self.model(s[index])[0]
                dist = Beta(alpha, beta)
                new_logp = dist.log_prob(a[index]).sum(dim=1, keepdim=True)
                ratio = new_logp.exp() / old_logp.view(-1, 1)[index].exp()

                surr1 = ratio * advantages[index]
                surr2 = torch.clamp(ratio, 1. - self.clip, 1. + self.clip) * advantages[index]

                actor_loss = -torch.min(surr1, surr2).mean()  # maximize advantage

                critic_loss = ((self.model(s[index])[1] - v_[index]) ** 2).mean()  # minimize diff between critics

                loss = actor_loss + critic_loss

                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

        self.buffer.clear()


def ppo_train(env, agent, n_episode, update_step):
    scores = []
    total_steps = 0
    learn_steps = 0
    best_score = float("-inf")

    for episode in range(n_episode):
        episode_steps = 0
        total_reward = 0

        state = env.reset()
        
        while True:
            action, logp, value = agent.select_action(state)
            action[0] = 2 * (action[0] - 0.5)
            next_state, reward, done = env.step(action)

            total_steps += 1
            episode_steps += 1
            total_reward += reward
            agent.memory((state, action, logp, reward, next_state, value))

            if total_steps % update_step == 0:
                print("...updating...")
                agent.learn()
                learn_steps += 1

            if done:
                break
            state = next_state

        scores.append(total_reward)
        avg_score = np.mean(scores[-10:]) # take the average of the last 10 elements

        if avg_score > best_score:
            agent.save_model()
            best_score = avg_score

        print(f"Epsode: {episode:04}, epsode steps: {episode_steps:04}, total steps: {total_steps:07}, learn steps: {learn_steps:04},",
              f"episode reward: {total_reward:1f}, avg reward: {avg_score:1f}")

    return scores

def ppo_test(env, agent, n_episode):
    scores = []
    total_steps = 0
    learn_steps = 0
    best_score = float("-inf")

    for episode in range(n_episode):
        episode_steps = 0
        total_reward = 0

        state = env.reset()

        while True:
            action, _, _ = agent.select_action(state)
            action_ = action * np.array([2., 1., 1.]) + np.array([-1., 0., 0.])
            next_state, reward, done = env.step(action_)
            total_steps += 1
            episode_steps += 1
            total_reward += reward
            if done:
                break
            state = next_state

        scores.append(total_reward)
        avg_score = np.mean(scores[-10:])
        if avg_score > best_score:
            best_score = avg_score

        print(f"Epsode: {episode:04}, epsode steps: {episode_steps:04}, total steps: {total_steps:07}, learn steps: {learn_steps:04},",
              f"episode reward: {total_reward:1f}, avg reward: {avg_score:1f}")
    return scores


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('-t', '--test', help='testing model', action='store_true', required=False)
    args = parser.parse_args()
    train = True
    if args.test:
        train = False
    if train:
        print("... start training ...")
        env = Env()
        agent = Agent(state_dim=3, action_dim=3)
        score = ppo_train(env, agent, n_episode=30000, update_step=500)

    else:
        print("... start testing ...")
        env = Env(render=True)
        agent = Agent(state_dim=3, action_dim=3, save_dir='./ppo_model')
        agent.load_model()
        scores = ppo_test(env, agent, n_episode=10)
        print(f"scores mean:{np.mean(scores)}, score std:{np.std(scores)}")
        np.save("ppo_car_racing_scores_100", scores)
    