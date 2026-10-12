import os
import io
import contextlib
from stable_baselines3 import PPO
from stable_baselines3.common.logger import configure
from env_toy import ToyTradingEnv

def train_with_clip(clip_range, name):
    print(f"\n{'='*50}")
    print(f"Running PPO Ablation Study: {name} (clip_range={clip_range})")
    print(f"{'='*50}")
    
    env = ToyTradingEnv()
    
    # We will capture stdout to grab the specific metrics
    log_dir = f"./tb_logs/ablation_{name}"
    os.makedirs(log_dir, exist_ok=True)
    
    model = PPO(
        "MlpPolicy", 
        env, 
        verbose=1, 
        tensorboard_log=log_dir,
        learning_rate=0.003,
        n_steps=16,
        batch_size=16,
        n_epochs=10,
        clip_range=clip_range,
        seed=42 # Fixed seed for fair comparison
    )
    
    model.learn(total_timesteps=500, tb_log_name=f"PPO_{name}")
    
    obs, info = env.reset()
    done = False
    truncated = False
    
    while not done and not truncated:
        action, _states = model.predict(obs, deterministic=True)
        obs, reward, done, truncated, info = env.step(action)
        
    print(f"[{name}] Final Net Worth: {info['net_worth']:.2f}")

if __name__ == "__main__":
    # 1. 論文推薦的最佳參數 Epsilon = 0.2
    train_with_clip(0.2, "Paper_Optimal_0.2")
    
    # 2. 幾乎無限制的更新 (類似傳統 Policy Gradient) Epsilon = 1.0
    # 這會讓模型在單次更新中大幅改變策略，容易導致崩潰或高度震盪
    train_with_clip(1.0, "Extreme_Unclipped_1.0")
