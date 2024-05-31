import gymnasium as gym
import numpy as np

gym.logger.set_level(10)

class Env:
    def __init__(self, action_stack = 4, render = False):
        if render: 
            self.env = gym.make('CarRacing-v2', render_mode = 'human')
        else:
            self.env = gym.make('CarRacing-v2')
        self.action_stack = action_stack

    def reset(self):
        state, _ = self.env.reset()
        for _ in range (30):
            state, _, _, _ , _ = self.env.step(np.array([0, 0 ,0]))
        self.reward_list = [0] * 100
        state = state[:84, 6:90]
        return np.moveaxis(state, -1, 0) / 255.0
    
    def step(self, action):
        total_reward = 0
        done = False
        for _ in range(self.action_stack):
            state, reward, terminated, truncated, _ = self.env.step(action)
            total_reward += reward
            self.update_reward(reward)
            if terminated or truncated or np.mean(self.reward_list) <= -0.1:
                done = True
                break
        state = state[:84, 6:90]
        return np.moveaxis(state, -1, 0) / 255.0, total_reward, done

    def update_reward(self, r):
        self.reward_list.pop(0)
        self.reward_list.append(r)
        assert len(self.reward_list) == 100