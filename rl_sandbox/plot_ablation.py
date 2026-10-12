import os
import matplotlib.pyplot as plt
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

def load_tb_data(log_dir, tags):
    # Find the events file in the directory
    event_files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if "events.out.tfevents" in f]
    if not event_files:
        print(f"No event files found in {log_dir}")
        return {}
        
    event_file = event_files[0]
    ea = EventAccumulator(event_file)
    ea.Reload()
    
    data = {}
    for tag in tags:
        try:
            events = ea.Scalars(tag)
            steps = [e.step for e in events]
            values = [e.value for e in events]
            data[tag] = (steps, values)
        except Exception as e:
            print(f"Tag {tag} not found in {log_dir}: {e}")
            data[tag] = ([], [])
            
    return data

def main():
    dir_optimal = "./tb_logs/ablation_Paper_Optimal_0.2/PPO_Paper_Optimal_0.2_1"
    dir_unclipped = "./tb_logs/ablation_Extreme_Unclipped_1.0/PPO_Extreme_Unclipped_1.0_1"
    
    # We want to compare these three metrics
    tags_to_plot = ["train/clip_fraction", "train/approx_kl", "train/entropy_loss"]
    
    data_optimal = load_tb_data(dir_optimal, tags_to_plot)
    data_unclipped = load_tb_data(dir_unclipped, tags_to_plot)
    
    # Create a figure with 3 subplots
    fig, axs = plt.subplots(3, 1, figsize=(10, 15))
    fig.suptitle("PPO Ablation Study: clip_range=0.2 (Optimal) vs clip_range=1.0 (Unclipped)", fontsize=16)
    
    titles = ["Clip Fraction", "Approximate KL Divergence", "Entropy Loss"]
    
    for i, tag in enumerate(tags_to_plot):
        ax = axs[i]
        
        # Plot Optimal
        steps_opt, vals_opt = data_optimal.get(tag, ([], []))
        if steps_opt:
            ax.plot(steps_opt, vals_opt, label="Optimal (0.2)", color="blue", linewidth=2)
            
        # Plot Unclipped
        steps_unc, vals_unc = data_unclipped.get(tag, ([], []))
        if steps_unc:
            ax.plot(steps_unc, vals_unc, label="Unclipped (1.0)", color="red", linewidth=2, linestyle="--")
            
        ax.set_title(titles[i])
        ax.set_xlabel("Timesteps")
        ax.set_ylabel(titles[i])
        ax.legend()
        ax.grid(True, alpha=0.3)
        
    plt.tight_layout()
    output_path = "C:/Users/User/.gemini/antigravity/brain/c6696e8c-8a08-44d7-9b90-5d47e61ebb20/ppo_ablation_plot.png"
    plt.savefig(output_path)
    print(f"Plot saved successfully to {output_path}")

if __name__ == "__main__":
    main()
